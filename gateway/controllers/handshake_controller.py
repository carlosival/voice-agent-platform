import os
import json
import asyncio
import logging
import jwt
from fastapi import APIRouter, Request, Depends, HTTPException, status, WebSocket, WebSocketException, WebSocketDisconnect
from starlette.websockets import WebSocketState
from gateway.controllers.helper import verify_token_credentials, verify_raw_token
from gateway.worker_router.worker_router import resolve_stream_key, AgentConfig, NoCapacityError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ALGORITHM = os.getenv("ALGORITHM", "HS256")
SECRET_KEY = os.getenv("GATEWAY_SECRET_KEY")
_CLIENT_MSG_TIMEOUT_S = 10.0
_WORKER_TOTAL_TIMEOUT_S = 60.0

if not SECRET_KEY:
    raise RuntimeError("GATEWAY_SECRET_KEY environment variable is not set.")


class WebRTCOffer(BaseModel):
    sdp: str
    type: str



class HandshakeController:


    def _get_client_ip(self, request: Request | WebSocket) -> str:
        """Resolves the client IP, handling reverse proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host


    async def ws_handshake(self, websocket: WebSocket, token: str) -> dict:
        """
        Handle the core logic for WebSocket handshake.
        Two-channel protocol:
        1. Client sends offer  → published to worker stream
        2. Worker sends answer → forwarded to client
        3. Trickle ICE runs bidirectionally until either side signals done
        """
        redis_client = websocket.app.state.redis
        current_ip = self._get_client_ip(websocket)

        # --- Decode & validate JWT ---
        try:
            token_decoded = verify_raw_token(token)
        except Exception as e:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            raise e

        session_id = token_decoded.get("session_id")
        agent_id   = token_decoded.get("agent_id")
        token_ip   = token_decoded.get("client_ip")
        pk_id      = token_decoded.get("pk_id")
        tier       = token_decoded.get("tier")
        regions    = token_decoded.get("regions")

        if not session_id or not agent_id or pk_id is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Token missing required claims: session_id, agent_id, pk_id.",
            )

        if token_ip and token_ip != current_ip:
            logger.warning(
                f"IP mismatch for session {session_id}: token={token_ip}, requester={current_ip}"
            )

        # --- Consume one-time session token from Redis ---
        token_redis = await redis_client.getdel(f"session:{session_id}")

        if not token_redis:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Session token invalid, expired, or already used.",
            )

        if token_redis != token:
            logger.warning(f"Token mismatch for session {session_id} — possible replay attack.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Token mismatch.",
            )

        # --- Resolve target worker stream ---
        agent = AgentConfig(
            agent_id=agent_id,
            tier=tier,
            regions=regions,
        )

        # --- Resolve target worker stream ---
        try:
            stream_key = await resolve_stream_key(agent, current_ip, redis_client)
            if not stream_key:
                logger.warning(f"No suitable worker for agent {agent_id}.")
                await websocket.close(code=1013)
                raise WebSocketException(code=1013, reason="No workers available in your region.")
        except NoCapacityError as e:
            logger.error(f"Capacity limit reached for tier {tier}: {e}")
            await websocket.close(code=1013)
            raise WebSocketException(code=1013, reason="Global capacity reached. Please try again later.")
        except Exception as e:
            logger.error(f"Routing failure for session {session_id}: {e}")
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            raise WebSocketException(code=status.WS_1011_INTERNAL_ERROR, reason="Internal routing failure.")

        # --- Redis stream keys ---
        answer_stream_key    = f"webrtc:answer:{session_id}" # worker forward -> client listen
        ice_stream_key_client       = f"webrtc:client:ice:{session_id}" # client forward -> worker listen
        ice_stream_key_worker = f"webrtc:worker:ice:{session_id}" # worker forward -> client listen


        state = {
            "answer_sent":     False,
            "client_ice_done": False,
            "answer_cursor":   "0-0",
            "ice_cursor":      "0-0",
        }

        await websocket.accept()

        # Define an isolated task to handle incoming WebSocket messages from the client
        async def listen_to_client():
            try:
                while True:
                    raw = await asyncio.wait_for(
                            websocket.receive_text(),
                            timeout=_CLIENT_MSG_TIMEOUT_S,
                        )
                    data = json.loads(raw)
                    msg_type = data.get("type")

                    if msg_type in ("offer", "answer"):
                        logger.info(f"[{session_id}] Reading offer/answer from client sending to worker")
                        await redis_client.xadd(stream_key, {
                            "session_id": session_id,
                            "agent_id":   agent_id,
                            "pk_id":      str(pk_id),
                            "type":       data["type"],
                            "sdp":        data["sdp"],
                        })

                    elif msg_type == "candidate":
                        logger.info(f"[{session_id}] Reading ICE candidate from client sending to worker")
                        cand_data = data.get("candidate")
                        
                        await redis_client.xadd(ice_stream_key_client, {
                            "payload": json.dumps(cand_data)
                        })

                        # Check if browser is signaling End-of-Candidates
                        if cand_data is None or cand_data.get("candidate") == "":
                            logger.info(f"[{session_id}] Browser finished sending ICE candidates.")
                            state["client_ice_done"] = True
                    else:
                        logger.warning(f"[{session_id}] Unknown message type from client: {msg_type!r}")
            except WebSocketDisconnect:
                logger.info("Client disconnected naturally.")
            except Exception as e:
                logger.error(f"Error in client listener: {e}")

        # Define an isolated task to read from Redis and push to the client WebSocket
        async def _polling_worker_loop():
            try:
                while True:
                    # Polling or listen from worker's answers and ice streams
                    results = await redis_client.xread(
                        {answer_stream_key: state["answer_cursor"], ice_stream_key_worker: state["ice_cursor"]},
                        count=10,
                        block=1000,  # Block for up to 1 second if no data to save CPU loops
                    ) 
                    
                    if not results:
                        continue

                    for stream_name, messages in results:
                        stream_name_str = stream_name.decode() if isinstance(stream_name, bytes) else stream_name

                        for msg_id, payload in messages:
                            # CRITICAL: Decode the message ID to update the cursor
                            msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id

                            # ── Answer ────────────────────────────────────────────
                            if stream_name_str == answer_stream_key:
                                state["answer_cursor"] = msg_id_str  # Advance cursor so we don't reread it!
                                
                                if not state["answer_sent"]:
                                    answer = json.loads(payload["payload"])
                                    logger.info(f"[{session_id}] Reading answer from worker sending to client")
                                    await websocket.send_text(json.dumps({
                                        "type": answer["type"],
                                        "sdp":  answer["sdp"],
                                    }))
                                    state["answer_sent"] = True  # Block further duplicate answers
                                    return

                            # ── ICE ────────────────────────────────────────────
                            elif stream_name_str == ice_stream_key_worker:
                                # Note: aiortc won't usually hit this block because of Vanilla SDP
                                state["ice_cursor"] = msg_id_str  # Advance cursor!   
                                ice_candidate = json.loads(payload["payload"])
                                logger.info(f"[{session_id}] Reading ICE candidate from worker sending to client")

                                await websocket.send_text(json.dumps({
                                    "type": ice_candidate["type"],
                                    "candidate":  ice_candidate["candidate"],
                                }))
                                
                                # Since aiortc generates a consolidated SDP answer, 
                                # your end_ice checks can trigger here if null candidates are passed.
                                if ice_candidate.get("candidate") == "":
                                    logger.info("Worker reached end-of-candidates.")
                                    return

            except Exception as e:
                logger.error(f"Error in worker listener: {e}")

        async def listen_to_worker() -> None:
            try:
                await asyncio.wait_for(
                    _polling_worker_loop(),
                    timeout=_WORKER_TOTAL_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.error("[%s] Worker timed out after %ss — no answer or ICE never completed.", session_id, _WORKER_TOTAL_TIMEOUT_S)
            except Exception as exc:
                logger.error("[%s] Error in worker listener: %s", session_id, exc)

        try:
            
            #Listen for worker data (answer and ice)-> send to client  and forward client data to worker.
            while True:

                # Run both loops concurrently. If either finishes (or fails), cancel the other.
                await asyncio.gather(
                    listen_to_client(),
                    listen_to_worker(),
                    return_exceptions=True
                )

            
        except WebSocketDisconnect:
            logger.info("Client disconnected naturally from inbound loop.")
        
        finally:
            logger.info("Session Handshake ended — cleaning queues")
            await asyncio.gather(
                redis_client.delete(answer_stream_key),
                redis_client.delete(ice_stream_key_client),
                redis_client.delete(ice_stream_key_worker),
                return_exceptions=True,
            )
            try:
                # Check the Starlette internal state to prevent calling send on a closed socket
                if websocket.client_state != starlette.websockets.WebSocketState.DISCONNECTED:
                    await websocket.close()
            except RuntimeError as e:
                # Catch "Cannot call send once a close message has been sent"
                logger.debug(f"Socket close skipped or already closing: {e}")

    
    async def handshake(self, request: Request, offer: WebRTCOffer, credentials) -> dict:
        """
        Handle the core logic for HTTP POST handshake.
        """
        redis_client = request.app.state.redis
        current_ip = self._get_client_ip(request)
        
        token_decoded = None

        try:
            # --- Decode & validate JWT ---
            token_decoded = verify_token_credentials(credentials)
        except Exception as e:
            raise e
        
        session_id = token_decoded.get("session_id")
        agent_id = token_decoded.get("agent_id")
        token_ip = token_decoded.get("client_ip")
        
        pk_id = token_decoded.get("pk_id")
        tier = token_decoded.get("tier")
        regions = token_decoded.get("regions")

        if not session_id or not agent_id or pk_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Token missing required claims: session_id, agent_id, pk_id.",
            )

        # Optional: Verify IP match if present in token
        if token_ip and token_ip != current_ip:
            logger.warning(f"IP mismatch for session {session_id}: token={token_ip}, requester={current_ip}")
            # Depending on policy, you might want to block this:
            # raise HTTPException(status_code=403, detail="IP mismatch.")

        offer_type = offer.type
        offer_sdp = offer.sdp

        # --- Consume one-time session token from Redis ---
        token_redis = await redis_client.getdel(f"session:{session_id}")

        if not token_redis:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session token invalid, expired, or already used.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # --- Verify token integrity (Redis value must match Bearer token) ---
        if token_redis != token_raw:
            logger.warning(f"Token mismatch for session {session_id} — possible replay attack.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token mismatch.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # --- Resolve target worker stream ---
        agent = AgentConfig(
            agent_id=agent_id,
            tier=tier,
            regions=regions
        )

        try:
            stream_key = await resolve_stream_key(agent, current_ip, redis_client)
            if not stream_key:
                logger.warning(f"No suitable worker found for agent {agent_id} in {regions or 'any'} with no fallback.")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No workers available in your region."
                )
        except NoCapacityError as e:
            logger.error(f"Capacity limit reached for tier {tier}: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Global capacity reached. Please try again later."
            )
        except Exception as e:
            logger.error(f"Routing failure for session {session_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal routing failure."
            )

        answer_stream_key = f"webrtc:answer:{session_id}"
 

        # --- Publish offer to worker stream ---
        await redis_client.xadd(stream_key, {
            "session_id": session_id,
            "agent_id": agent_id,
            "pk_id": pk_id,
            "type": offer_type,
            "sdp": offer_sdp,
        })
 
        # --- Wait for worker answer, then cleanup ---
        try:
            async with asyncio.timeout(10):
                while True:
                    results = await redis_client.xread(
                        {answer_stream_key: "0-0"},
                        count=1,
                        block=5000,
                    )

                    if not results:
                        continue

                    _, messages = results[0]
                    _, data = messages[0]

                    return json.loads(data[b"payload"])

        except asyncio.TimeoutError:
            logger.error(f"Handshake timeout for session {session_id}")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Media worker timeout.",
            )

        finally:
            await redis_client.delete(answer_stream_key)
            
        

    
        
       