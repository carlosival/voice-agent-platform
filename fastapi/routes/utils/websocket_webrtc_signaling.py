import logging
import json
import os
import asyncio
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from aiortc import RTCSessionDescription, RTCIceCandidate
from aiortc.sdp import candidate_from_sdp
from routes.utils.webrtc_peer import create_peer
from audio_track import AudioOutputTrack
from uuid import uuid4

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")
WS_URL   = BASE_URL.replace("https://", "wss://").replace("http", "ws") + "/ws/"

# ── WebSocket — no origin check (handles ws:// and wss://) ────────────────
ALLOWED_ORIGINS = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:8080",
    "https://epiphanic-marriageable-keely.ngrok-free.dev",
    "null",                       # file:// sends "null" as origin
]

async def signaling_handler(ws: WebSocket):

    # ── Origin check ──────────────────────────────────────────────────────
    origin = ws.headers.get("origin", "null")
    if origin not in ALLOWED_ORIGINS:
        logger.warning(f"Rejected connection from origin: {origin}")
        await ws.close(code=1008)
        return

    await ws.accept()
    logger.info(f"WebSocket connected from origin: {origin}")
    peer_session = await create_peer(ws)
    pc = peer_session.pc
    ctx = peer_session.ctx
    tasks = peer_session.tasks
    output_track = ctx.shared_data["resources"]["output_track"]

    try:
        while True:                                           # ✅ if/elif now inside loop

            message = await ws.receive_text()

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {message}")
                continue

            msg_type = data.get("type")

            # ── WebRTC offer  handler ───────────────────────────────────────────────
            if msg_type == "offer":
                logger.info("Received WebRTC offer")

                offer = RTCSessionDescription(
                    sdp=data["sdp"],
                    type=data["type"]
                )

                await pc.setRemoteDescription(offer)

                 # ✅ Find the audio transceiver aiortc just created from the offer
                for transceiver in pc.getTransceivers():
                    if transceiver.kind == "audio":
                        transceiver.direction = "sendrecv"
                        # ✅ Assign your output track as the sender track
                        transceiver.sender.replaceTrack(output_track)
                        logger.info(f"Audio transceiver configured: {transceiver.direction}")
                    elif transceiver.kind == "video":
                        # Server ONLY receives client video (does not send back)
                        # The server's direction must be the MIRROR of the client
                        transceiver.direction = "recvonly"
                        logger.info(f"Video transceiver configured: {transceiver.direction}")
    
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                await ws.send_text(json.dumps({
                    "type": pc.localDescription.type,
                    "sdp":  pc.localDescription.sdp
                }))

                logger.info("Sent WebRTC answer: %s", pc.localDescription.sdp)

            # ── Text message ───────────────────────────────────────────────
            elif msg_type == "text":                          # ✅ was "if", broke elif chain
                text = data.get("content") or data.get("text", "")
                logger.info(f"Text message: {text}")

                asyncio.ensure_future(
                    handle_text_and_respond(text, ctx)
                )

            # ── ICE candidate ──────────────────────────────────────────────
            elif msg_type == "candidate":
                raw = data.get("candidate")
                if raw and raw.get("candidate"):
                    try:
                        candidate_str = raw["candidate"]

                        # Strip "candidate:" prefix if present
                        if candidate_str.startswith("candidate:"):
                            candidate_str = candidate_str[len("candidate:"):]

                        candidate             = candidate_from_sdp(candidate_str)
                        candidate.sdpMid        = raw.get("sdpMid")
                        candidate.sdpMLineIndex = raw.get("sdpMLineIndex")

                        await pc.addIceCandidate(candidate)
                        logger.info(f"ICE candidate added: {candidate.type} via {candidate.protocol}")

                    except Exception as e:
                        logger.warning(f"Failed to add ICE candidate: {e} | raw={raw}")

    # ── Disconnect handling ────────────────────────────────────────────────
    except WebSocketDisconnect as e:                          # ✅ correct exception for FastAPI
        # code 1000 = clean close, 1006 = abrupt drop (curl, network cut)
        logger.info(f"Client disconnected: code={e.code}")

    except Exception as e:
        logger.error(f"Unexpected error in signaling handler: {e}", exc_info=True)

    finally:
        
        # 1. Kill all the background pipelines tasks
        # When the connection dies, kill all associated tasks
        # Cancel everything first
        for task in tasks:
            if not task.done():
                task.cancel()

        # Then await them all at once
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Pipeline task cancelled.")
       
        # 2. Close WebRTC (This clears ICE, Transceivers, etc.)
        await pc.close()
        logger.info("Peer connection closed.")



async def handle_text_and_respond(text: str, ctx):
    """
    I going to call directly the LLM, refactor to use a appropiate workflow.
    """
    from utils import call_llm_stream, _build_chat_messages
    try:
        msg_id = f"{ctx.shared_data['session_id']}:{uuid4()}"
        http_client = ctx.shared_data["resources"]["http_client"]
        history_messages = ctx.shared_data["message_history"]
        ws = ctx.shared_data["resources"]["ws"]
        full_response = ""
        idx = 0
        await history_messages.add_user_message(text)
        messages = _build_chat_messages(await history_messages.get_messages())

        async for chunk in call_llm_stream(http_client, messages):
            await ws.send_text(json.dumps({
                "id": f"{msg_id}",
                "type":    "chat",
                "chunk": chunk,
                "done":    False
            }))
            idx += 1
            full_response += chunk
        
        await history_messages.add_ai_message(full_response, None)
        await ctx.shared_data["resources"]["ws"].send_json({
            "id": f"{msg_id}",
            "chunk": "",
            "done": True
        })

    except Exception as e:
        logger.error(f"Pipeline error: {e}")