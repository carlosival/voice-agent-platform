import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel
from gateway.controllers import HandshakeController

router = APIRouter()
logger = logging.getLogger(__name__)

handshake_controller = HandshakeController()

class WebRTCOffer(BaseModel):
    sdp: str
    type: str

@router.post("/handshake")
async def WebRTC_handshake(request: Request):
    """
    Handle WebRTC handshake by delegating to HandshakeController.
    """
    return await handshake_controller.handshake(request)