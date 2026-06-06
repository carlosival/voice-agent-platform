import os
import logging
import httpx
from fastapi import APIRouter, HTTPException, status

logger = logging.getLogger(__name__)
router = APIRouter()

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
    # We log a warning instead of raising RuntimeError at module level to allow app to start
    # but the endpoint will fail gracefully.
    logger.warning("Missing Cloudflare Environment Variables! ICE server generation will fail.")


@router.get("/ice-servers")
async def get_ice_servers():
    """
    Generate temporary ICE server credentials via Cloudflare RTC API.
    """
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ICE server configuration is missing on the server."
        )

    cloudflare_url = f"https://rtc.live.cloudflare.com/v1/turn/keys/{CLOUDFLARE_ACCOUNT_ID}/credentials/generate-ice-servers"
    
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Request credentials that expire in 10 minutes (600 seconds)
    payload = {
        "ttl": 600 
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(cloudflare_url, headers=headers, json=payload)
            
            if response.status_code != 200:
                logger.error(f"Cloudflare API Error: {response.text}")
                raise HTTPException(
                    status_code=response.status_code, 
                    detail="Failed to retrieve ICE servers from provider."
                )
            
            return response.json()
            
        except httpx.RequestError as exc:
            logger.error(f"HTTP Request to Cloudflare failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Connectivity issue with external ICE provider."
            )