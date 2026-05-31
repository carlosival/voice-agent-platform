
import os
from fastapi import APIRouter, HTTPException, Request
import redis
import jwt
import datetime
import httpx


logger = logging.getLogger(__name__)
router = APIRouter()


CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
    raise RuntimeError("Missing Cloudflare Environment Variables!")


@router.get("/get/ice-servers")
async def get_ice_servers():
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
                raise HTTPException(
                    status_code=response.status_code, 
                    detail=f"Cloudflare API Error: {response.text}"
                )
            
            data = response.json()
            """ Expected response from Cloudflare pass it as is to the client
            {
                "iceServers": [
                {
                "urls": [
                    "stun:stun.cloudflare.com:3478",
                    "turn:turn.cloudflare.com:3478?transport=udp",
                    "turn:turn.cloudflare.com:3478?transport=tcp",
                    "turns:turn.cloudflare.com:5349?transport=tcp"
                ],
                "username": "xxxx",
                "credential": "yyyy",
                }
            ]
            } """
            
            return data
            
        except httpx.RequestError as exc:
            raise HTTPException(status_code=500, detail=f"HTTP Request failed: {exc}")