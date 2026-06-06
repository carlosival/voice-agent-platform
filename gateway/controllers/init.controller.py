import os
import uuid
import jwt
import datetime
import logging
from typing import Optional, Tuple
from fastapi import HTTPException, status, Request

from gateway.db.repositories.pk.repo import PKRepository
from gateway.db.repositories.user.repo import UserRepository

logger = logging.getLogger(__name__)

DOMAIN = os.getenv("DOMAIN", "localhost")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
TOKEN_EXPIRATION_SECONDS = int(os.getenv("TOKEN_EXPIRATION_SECONDS", 60))
SECRET_KEY = os.getenv("GATEWAY_SECRET_KEY")

class InitController:
    
    async def get_token(self, request: Request) -> dict:
        """
        Handle the core logic for session initialization.
        """

        # --- Parse & validate input ---
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body.")

        pk_body = data.get("pk")
        agent_id = data.get("agent_id")
        client_origin = data.get("client_origin")

        if not pk_body or not agent_id or not client_origin:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing required fields: pk, agent_id, client_origin."
            )

        # --- Validate agent_id format ---
        try:
            agent_uuid = uuid.UUID(agent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Agent ID format.")

        # --- DB operations ---
        async with request.app.state.db() as db:
            pk_repo = PKRepository(db)
            user_repo = UserRepository(db)

            # Step 1: Validate Public Key and Origin
            key_record = await pk_repo.check_pk(pk_body, client_origin)

            if not key_record:
                logger.warning(
                    f"Unauthorized session request: pk invalid or origin '{client_origin}' not allowed."
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid public key credential or unauthorized domain."
                )

            # Step 2: Verify User-Agent Authorization
            auth_result = await user_repo.user_agent_authorized(key_record.id, agent_uuid)

            if not auth_result:
                logger.warning(
                    f"User {key_record.user_id} not authorized for agent {agent_id}."
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,  # 403, not 401 — identity is known
                    detail="You do not have permission to access this voice agent."
                )

        # --- Generate token ---
        session_id = str(uuid.uuid4())
        expiration = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=TOKEN_EXPIRATION_SECONDS)
        token_claims = {
            "sub": str(key_record.user_id),
            "session_id": session_id,
            "pk": pk_body,
            "agent_id": agent_id,
            "exp": expiration,
        }

        try:
            signed_token = jwt.encode(token_claims, SECRET_KEY, algorithm=ALGORITHM)
        except Exception as e:
            logger.error(f"JWT encoding failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal signing failure during initialization."
            )

        # --- Persist session to Redis ---
        try:
            redis = request.app.state.redis
            await redis.set(f"session:{session_id}", signed_token, ex=TOKEN_EXPIRATION_SECONDS)
        except Exception as e:
            logger.error(f"Redis set failed for session {session_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist session."
            )

        return {
            "session_id": session_id,
            "token": signed_token,
        }


    
