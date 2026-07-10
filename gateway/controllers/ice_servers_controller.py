
import os
import logging
import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi import APIRouter, Request, Depends, HTTPException, status, WebSocket, WebSocketException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from gateway.controllers.helper import verify_token_credentials, verify_raw_token, _get_client_ip
from services import fetch_cloudflare_ice_servers

logger = logging.getLogger(__name__)
router = APIRouter()

security = HTTPBearer()

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
    # We log a warning instead of raising RuntimeError at module level to allow app to start
    # but the endpoint will fail gracefully.
    logger.warning("Missing Cloudflare Environment Variables! ICE server generation will fail.")


class ICEController:

    async def get_ice_servers(self, request: Request, credentials: HTTPAuthorizationCredentials=Depends(security)):

        redis_client = request.app.state.redis
        current_ip = _get_client_ip(request)

        # --- Decode & validate JWT ---
        try:
            token_decoded = verify_token_credentials(credentials)
            #Check if token is in redis with out extract it
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session token invalid, expired, or already used.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token_ip   = token_decoded.get("client_ip")

        if token_ip and token_ip != current_ip:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session ip mismatch."
            )
            logger.warning(
                f"IP mismatch for session {session_id}: token={token_ip}, requester={current_ip}"
            )

        response_json = await fetch_cloudflare_ice_servers()
        return response_json

        