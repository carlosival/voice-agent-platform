import os
import json
import asyncio
import logging
import jwt
from fastapi import APIRouter, Request, Depends, HTTPException, status, WebSocket
from gateway.controllers.helper import verify_token
from gateway.worker_router.worker_router import resolve_stream_key, AgentConfig, NoCapacityError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ALGORITHM = os.getenv("ALGORITHM", "HS256")
SECRET_KEY = os.getenv("GATEWAY_SECRET_KEY")

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
        token_decoded = verify_token_credentials(token)
    except Exception as e:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise e

    session_id = token_decoded.get("session_id")
    agent_id   = token_decoded.get("agent_id")
    token_ip   = token_decoded.get("client_ip")
    pk_id      = token_decoded.get("pk_id")
    tier       = token_decoded.get("tier")
    region     = token_decoded.get("region")

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
    # Gateway → Worker  (offer + client ICE)
    # Worker  → Gateway (answer + worker ICE)  ─ keyed by message type inside payload
    answer_stream_key    = f"webrtc:answer:{session_id}"
    ice_client_stream    = f"webrtc:ice:client:{session_id}"   # client → worker (via Redis)
    ice_worker_stream    = f"webrtc:ice:worker:{session_id}"   # worker → client (via Redis)

    await websocket.accept()

    # ─────────────────────────────────────────────────────────────────────
    # INBOUND  — client → Redis
    # Reads every message the client sends and routes it to the correct
    # worker stream based on the "type" field.
    # ─────────────────────────────────────────────────────────────────────
    async def inbound():
        while True:
            try:
                raw = await websocket.receive_text()
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(f"[{session_id}] Invalid JSON from client: {e}")
                continue
            except WebSocketDisconnect:
                logger.info(f"[{session_id}] Client disconnected (inbound)")
                break
            except Exception as e:
                logger.error(f"[{session_id}] Inbound error: {e}", exc_info=True)
                break

            msg_type = data.get("type")

            if msg_type == "offer":
                logger.info(f"[{session_id}] Forwarding offer to worker")
                await redis_client.xadd(stream_key, {
                    "session_id": session_id,
                    "agent_id":   agent_id,
                    "pk_id":      str(pk_id),
                    "type":       data["type"],
                    "sdp":        data["sdp"],
                })

            elif msg_type == "candidate":
                candidate = data.get("candidate", {})
                logger.debug(f"[{session_id}] Forwarding client ICE candidate")
                await redis_client.xadd(ice_client_stream, {
                    "session_id":    session_id,
                    "candidate":     candidate.get("candidate", ""),
                    "sdpMid":        candidate.get("sdpMid") or "",
                    "sdpMLineIndex": str(candidate.get("sdpMLineIndex", "")),
                })

            else:
                logger.warning(f"[{session_id}] Unknown message type from client: {msg_type!r}")

    # ─────────────────────────────────────────────────────────────────────
    # OUTBOUND — Redis → client
    # Polls both worker streams (answer + worker ICE) and forwards each
    # message to the client in the original format the client expects.
    # ─────────────────────────────────────────────────────────────────────
    async def outbound():
        answer_cursor = "0-0"
        ice_cursor    = "0-0"

        while True:
            try:
                # Poll both streams in a single round-trip
                results = await redis_client.xread(
                    {
                        answer_stream_key: answer_cursor,
                        ice_worker_stream: ice_cursor,
                    },
                    count=10,
                    block=5000,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{session_id}] Outbound Redis read error: {e}", exc_info=True)
                break

            if not results:
                continue

            for stream_name, messages in results:
                for msg_id, payload in messages:
                    stream_name_str = stream_name.decode() if isinstance(stream_name, bytes) else stream_name

                    # ── Answer ────────────────────────────────────────────
                    if stream_name_str == answer_stream_key:
                        answer_cursor = msg_id

                        # Lifecycle event — no payload field
                        if b"event" in payload:
                            event = payload[b"event"].decode()
                            if event == "connected":
                                await websocket.close(code=1000)
                                return # exits outbound loop
                            continue

                        answer = json.loads(payload[b"payload"])
                        logger.info(f"[{session_id}] Forwarding answer to client")
                        await websocket.send_text(json.dumps({
                            "type": answer["type"],
                            "sdp":  answer["sdp"],
                        }))

                    # ── Worker ICE candidate ──────────────────────────────
                    elif stream_name_str == ice_worker_stream:
                        ice_cursor = msg_id
                        candidate_str = payload.get(b"candidate", b"").decode()
                        logger.debug(f"[{session_id}] Forwarding worker ICE candidate to client")
                        await websocket.send_text(json.dumps({
                            "type": "candidate",
                            "candidate": {
                                "candidate":     candidate_str,
                                "sdpMid":        payload.get(b"sdpMid", b"").decode() or None,
                                "sdpMLineIndex": int(v) if (v := payload.get(b"sdpMLineIndex", b"").decode()) else None,
                            }
                        }))

                        # End-of-candidates sentinel — worker is done trickling
                        if candidate_str == "":
                            logger.info(f"[{session_id}] Worker ICE complete — closing outbound")
                            return

    # ─────────────────────────────────────────────────────────────────────
    # Run both directions concurrently.
    # When either finishes (client disconnect or ICE complete), cancel the
    # other so we don't leave a dangling Redis poller or receive loop.
    # ─────────────────────────────────────────────────────────────────────
    try:
        inbound_task  = asyncio.create_task(inbound())
        outbound_task = asyncio.create_task(outbound())

        done, pending = await asyncio.wait(
            {inbound_task, outbound_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"[{session_id}] ws_handshake error: {e}", exc_info=True)

    finally:
        logger.info(f"[{session_id}] Session ended — cleaning up")
        await redis_client.delete(answer_stream_key)
        await redis_client.delete(ice_worker_stream)
        await redis_client.delete(ice_client_stream)
        if not websocket.client_state.value == 3:  # 3 = DISCONNECTED
            await websocket.close()


    
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
            
        

    
        
       