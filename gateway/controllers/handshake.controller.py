import os
import json
import asyncio
import logging
import jwt
from fastapi import HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

ALGORITHM = os.getenv("ALGORITHM", "HS256")
SECRET_KEY = os.getenv("GATEWAY_SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError("GATEWAY_SECRET_KEY environment variable is not set.")

bearer_scheme = HTTPBearer()



class HandshakeController:

    async def handshake(self, request: Request) -> dict:
        """
        Handle the core logic for WebRTC handshake.
        """
        redis_client = request.app.state.redis

        # --- Extract Bearer token from Authorization header ---
        authorization = request.headers.get("Authorization")

        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or malformed Authorization header.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token_raw = authorization.removeprefix("Bearer ").strip()

        if not token_raw:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token is empty.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # --- Decode & validate JWT ---
        try:
            token_decoded = jwt.decode(token_raw, SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token received: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        session_id = token_decoded.get("session_id")
        agent_id = token_decoded.get("agent_id")

        if not session_id or not agent_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Token missing required claims: session_id, agent_id.",
            )

        # --- Parse body ---
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON body."
            )

        offer = data.get("offer")

        if not offer:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing required field: offer."
            )

        offer_type = offer.get("type")
        offer_sdp = offer.get("sdp")

        if not offer_type or not offer_sdp:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing required offer fields: type, sdp."
            )

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

        # --- Publish offer and wait for worker answer ---
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"webrtc:answer:{session_id}")

        try:
            await redis_client.xadd("webrtc:offers", {
                "session_id": session_id,
                "agent_id": agent_id,
                "type": offer_type,
                "sdp": offer_sdp,
            })

            # 3. Wait for worker to publish the answer
            try:
                async with asyncio.timeout(10):
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            return json.loads(message["data"])
            except asyncio.TimeoutError:
                logger.error(f"Handshake timeout for session {session_id}")
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Media worker timeout."
                )

        finally:
            await pubsub.unsubscribe(f"webrtc:answer:{session_id}")
            await pubsub.aclose()

        

    
        
       