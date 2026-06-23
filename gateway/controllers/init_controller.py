import os
import uuid
import jwt
import datetime
import logging
import json
from typing import Optional, Tuple
from fastapi import HTTPException, status, Request
from pydantic import BaseModel, Field
from gateway.db.repositories.pk_repo import PKRepository
from gateway.db.repositories.user_repo import UserRepository
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DOMAIN = os.getenv("DOMAIN", "localhost")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
TOKEN_EXPIRATION_SECONDS = int(os.getenv("TOKEN_EXPIRATION_SECONDS", 60))
AGENT_CACHE_TTL_SECONDS = int(os.getenv("AGENT_CACHE_TTL_SECONDS", 86400))
SECRET_KEY = os.getenv("GATEWAY_SECRET_KEY")


# ── REQUEST/RESPONSE SCHEMAS ────────────────────────────────────────
class SessionInitializeRequest(BaseModel):
    pk: str = Field(description="The public key body of the connecting client application")
    agent_id: str = Field(description="The unique string identifier of the target Voice Agent")


class SessionInitializeResponse(BaseModel):
    connection_url: str
    token: str

class InitController:
    
    def _get_client_ip(self, request: Request) -> str:
        """Resolves the client IP, handling reverse proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host

    async def get_token(self, request: Request, body: SessionInitializeRequest) -> dict:
        # --- Parse & validate input ---
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body.")

        pk = body.pk
        agent_id = body.agent_id
        client_ip = self._get_client_ip(request)
        client_origin = request.headers.get("origin") or request.headers.get("referer")
        parsed = urlparse(client_origin)
        client_domain = parsed.hostname
        
        logger.info(f"Client origin: {client_origin}")
        logger.info(f"Client ip: {client_ip}")
        logger.info(f"Client request: {request}")

        if not pk or not agent_id or not client_origin:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing required fields: pk, agent_id, client_origin."
            )

        redis = request.app.state.redis

        # --- Validate agent_id format ---
        try:
            agent_uuid = uuid.UUID(agent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Agent ID format.")


        # ── 2. Validate public key + origin (Refactored for cache-first) is key is change in db, remove from cache ──
        #
        # We deliberately do NOT cache PK validation. Keys can be revoked
        # (is_active=False) at any time and must be checked on every request.
        async with request.app.state.db() as db:
            pk_repo   = PKRepository(db)
            key_record = await pk_repo.check_pk(pk, client_domain)

        if not key_record:
            logger.warning("Unauthorized: pk invalid or origin '%s' not allowed.", client_domain)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid public key credential or unauthorized domain.",
            )

        # ── 3. Resolve agent config (cache-first) ────────────────────
        #
        # Cache key is (public_key_id × agent_id) so each user's custom
        # overrides are stored separately. TTL is intentionally shorter
        # than the session TTL so config changes propagate reasonably fast.
        agent_config: Optional[dict] = None
        cache_key = f"agent_cfg:{key_record.id}:{agent_id}"

        try:
            cached = await redis.get(cache_key)
            if cached:
                agent_config = json.loads(cached)
                logger.debug("Agent config cache HIT: %s", cache_key)
        except Exception as exc:
            # Redis read failures are non-fatal: fall through to DB
            logger.warning("Redis GET failed for agent config (%s): %s", cache_key, exc)

        if agent_config is None:
            logger.debug("Agent config cache MISS: %s", cache_key)
            # --- DB operations after cache miss ---
            async with request.app.state.db() as db:
                user_repo   = UserRepository(db)
                agent_config = await user_repo.user_agent_config(
                    key_record.id, agent_uuid
                )

            if agent_config is None:
                logger.warning(
                    "User pk_id=%s not authorized for agent %s.", key_record.id, agent_id
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to access this voice agent.",
                )

            # Populate cache cache some agent data
            try:
                await redis.set(cache_key, json.dumps(agent_config), ex=AGENT_CACHE_TTL_SECONDS)
                logger.debug("Agent config cached: %s (TTL=%ds)", cache_key, AGENT_CACHE_TTL_SECONDS)
            except Exception as exc:
                # Non-fatal: we already have the config; next request will retry the cache
                logger.warning("Redis SET failed for agent config (%s): %s", cache_key, exc)

        # --- Generate token ---
        session_id = str(uuid.uuid4())
        expiration = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=TOKEN_EXPIRATION_SECONDS)
        token_claims = {
            "tier": agent_config.get("tier"),
            "regions": agent_config.get("regions"),
            "session_id": session_id,
            "pk_id": str(key_record.id),
            "agent_id": agent_id,
            "client_ip": client_ip,
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
            "token": signed_token,
            "connection_url": f"wss://{DOMAIN}/v1/handshake/{signed_token}",
        }


    
