import logging
from fastapi import APIRouter, Request, WebSocket, Depends
from pydantic import BaseModel
from gateway.controllers import HandshakeController, WebRTCOffer
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

router = APIRouter()
logger = logging.getLogger(__name__)

handshake_controller = HandshakeController()

security = HTTPBearer()

@router.post("/handshake")
async def http_handshake(request: Request,
    offer: WebRTCOffer,
    credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Handle HTTP POST handshake 
    """
    return await handshake_controller.handshake(request, offer, credentials)

@router.websocket("/handshake/{token}")
async def ws_handshake(websocket: WebSocket, token: str):
    """
    Handle WebSocket handshake 
    """
    return await handshake_controller.ws_handshake(websocket, token)