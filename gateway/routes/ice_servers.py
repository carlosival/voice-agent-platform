import os
import logging
import httpx
from fastapi import APIRouter, HTTPException, status, Depends, Request
from gateway.controllers import ICEController
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)
router = APIRouter()

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
    # We log a warning instead of raising RuntimeError at module level to allow app to start
    # but the endpoint will fail gracefully.
    logger.warning("Missing Cloudflare Environment Variables! ICE server generation will fail.")


ice_controller = ICEController()

security = HTTPBearer()

@router.get("/api/get_ice_servers")
async def get_ice_servers(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):

    return await ice_controller.get_ice_servers(request, credentials)

    