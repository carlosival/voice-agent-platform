import logging
from fastapi import APIRouter, WebSocket
from pydantic import BaseModel
from aiortc import RTCSessionDescription
from routes.utils.websocket_webrtc_signaling import signaling_handler


logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/")
async def websocket_offer(websocket: WebSocket):
    await signaling_handler(websocket)


