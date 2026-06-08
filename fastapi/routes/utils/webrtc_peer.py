import logging
import asyncio
import os
from fastapi import WebSocket
from starlette.websockets import WebSocketState
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, MediaStreamError
from audio_track import AudioOutputTrack
from steps import track_frames
from yaafpy.types import ExecContext
from flows import VOICE_WORKFLOW
from uuid import uuid4
from typing import AsyncGenerator
import httpx
import time
from utils.memory.in_memory import InMemoryMemory
from utils.tools.tools import EndConversationTool



logger = logging.getLogger(__name__)
active_connections = set()

# INPUT of CREATE PEER Connection
class PeerDependencies:
    audio_handler: Callable[[track], None]
    video_handler: Callable[[track], None]
    data_handler: Callable[[track], None]
    on_connected_fully: Callable[[], None] =lambda: ws.close(code=1000),  # ← WebSocket callback  
    on_track: Callable[[track], None]
    on_ice_state_change: Callable[[str], None] = lambda state: logger.info(f"ICE state: {state}"),
    on_connection_state_change: Callable[[str], None] = lambda state: logger.info(f"Connection state: {state}")

# OUTPUT CREATE PEER Connection
class PeerSession:
    def __init__(self):
        self.pc = None
        self.ctx = None
        self.output_track = None
        self.tasks = set()
        self.memory = InMemoryMemory()

    def add_task(self, task):
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
    def set_pc(self, pc):
        self.pc = pc
    def set_ctx(self, ctx):
        self.ctx = ctx
    def set_output_track(self, output_track):
        self.output_track = output_track

async def create_peer(ws: WebSocket):
    peer_session = PeerSession()
    
    config = RTCConfiguration(iceServers=[
        # Primary — Cloudflare STUN (already in your ecosystem)
        RTCIceServer(urls="stun:stun.cloudflare.com:3478"),

        # Fallback — Google STUN
        RTCIceServer(urls="stun:stun.l.google.com:19302"),
        RTCIceServer(urls="stun:stun1.l.google.com:19302"),
    ])

    pc = RTCPeerConnection(configuration=config)
    peer_session.set_pc(pc)

    output_track = AudioOutputTrack()
    peer_session.set_output_track(output_track)

    end_conversation_tool = EndConversationTool()
    
    session_id = str(uuid4())

    # Generate a deterministic ID based on a seed
    session_trace_id = ws.app.state.tracer.create_trace_id(seed=session_id)

    ctx = ExecContext(shared_data={
        "tools": {f"{end_conversation_tool.name}": end_conversation_tool},
        "session_id": session_id,
        "trace_context": {"trace_id": session_trace_id, "parent_span_id": ""},
        "peer_state": {
            "connected_at": time.time(),
            "last_activity": time.time(),
        },
        "metadata": {
            "token_usage": 0,
        },
        "message_history": peer_session.memory,
        "resources": {
            "output_track": output_track,
            "http_client": ws.app.state.http,
            "tracer": ws.app.state.tracer,
        }
    })


    peer_session.set_ctx(ctx)

    logger.info(f"Output track added: {output_track.id} kind={output_track.kind}")  
    
    # Add connection to set or Database
    active_connections.add(peer_session)
    
    logger.info(f"Peer created — active connections: {len(active_connections)}")

    # ── ICE state logging ─────────────────────────────────────────────────
    @pc.on("iceconnectionstatechange")
    async def on_ice_state():
        logger.info(f"ICE state: {peer_session.pc.iceConnectionState}")
        if peer_session.pc.iceConnectionState == "failed":
            logger.error("ICE failed — no valid path found")
        if peer_session.pc.iceConnectionState in ["connected", "completed"]:
            logger.info("ICE connected")
            if peer_session.pc.connectionState == "connected": # A fully established connection
                logger.info("PeerConnection established — closing WebSocket")
                await ws.close(code=1000)

    # ── Connection state logging ───────────────────────────────────────────
    @pc.on("connectionstatechange")
    async def on_conn_state():
        logger.info(f"Connection state: {peer_session.pc.connectionState}")
        if peer_session.pc.connectionState in ("failed", "closed"):
            # When the connection dies, kill all associated tasks
            for task in peer_session.tasks:
                if not task.done():
                    task.cancel()
            active_connections.discard(peer_session)
            logger.info(f"Peer removed — active connections: {len(active_connections)}")
        
        if peer_session.pc.connectionState in ["connected", "completed"]:
            logger.info("PeerConnection established")
            if peer_session.pc.iceConnectionState in ["connected", "completed"]: # A fully established connection
                logger.info("PeerConnection and ICE connected — closing WebSocket")
                await ws.close(code=1000)


    # ── Track handler ─────────────────────────────────────────────────────
    @pc.on("track")
    async def on_track(track):
        logger.info(f"Track received: kind={track.kind} id={track.id}")

        if track.kind == "audio":
            # Create the task and add it to our managed set
            task = asyncio.create_task(audio_pipeline(track, ctx))
            peer_session.add_task(task)
            # Remove from set when done to prevent memory leak
            # task.add_done_callback(pc._managed_tasks.discard)

        elif track.kind == "video":
            asyncio.ensure_future(_process_video(track))

    return peer_session



async def audio_pipeline(input_track, ctx):
    """ Process incoming audio frames — pipe to processing pipeline workflow
        Entry point called by the aiortc 'track' event.
        Creates session context and runs the workflow.
    """

    """ 
    ExecContext:
        - session_id: Unique identifier for the session
        - metadata: Metadata for the session
        - message_history: Message history for the session
        - events: Events for the session
        - resources: Resources for the session
        - *config: Configuration for the session to set MAX_TOKENS,PERMISSIONS, etc.
    """

    source = track_frames(input_track)
    output_track = ctx.shared_data["resources"]["output_track"]
    logger.info("Pipeline starting")
    trace_id = ctx.shared_data["trace_context"]["trace_id"]
    pc = ctx.shared_data["resources"]["peer_connection"]
    
    # 1. Manually start the root observation — no context manager needed
    session_trace = ctx.shared_data["resources"]["tracer"].start_observation(
        name="voice_call_session",
        trace_context={"trace_id": trace_id},
        as_type="agent",
        metadata={
            "tools": list(ctx.shared_data["tools"].keys()),
            "source": "webrtc",
            "resources": list(ctx.shared_data["resources"].keys())
        
        }
    )
    
    ctx.shared_data["trace_context"]["parent_span_id"] = session_trace.id

    final_closure_status = "completed_normally"

    try:
        async for _ in VOICE_WORKFLOW.run(source, ctx):
            pass
    except asyncio.CancelledError:
        logger.info("Pipeline cancelled (Normal shutdown or client disconnect)")
        final_closure_status = "client_disconnect_or_barge_in_shutdown"
        # Log the interruption info immediately to the trace instance
        
        session_trace.update(
            level="WARNING",
            status_message="Pipeline execution loop was cancelled by the runtime engine."
        )
  
    except httpx.ReadTimeout:
        logger.error("Pipeline Error: External API timed out (STT/LLM/TTS).")
        # Optional: You could send a message to the UI here via the websocket
        final_closure_status = "api_timeout_failure"
        
        session_trace.update(
            level="ERROR",
            status_message=f"Network Timeout Encountered: {str(e)}"
        )
        
    except Exception as e:
        # This catches everything else to prevent the 'Task exception was never retrieved' error
        logger.error(f"Pipeline Critical Failure: {type(e).__name__} - {e}", exc_info=True)
        final_closure_status = "critical_pipeline_crash"
        
        session_trace.update(
            level="ERROR",
            status_message=f"Pipeline crashed with uncaught error: {type(e).__name__} - {str(e)}"
        )
        
    finally:

        # 1. Ensure the output track is cleared and resources are freed
        try:    
            # Stop MediaStreamTrack incoming frames
            input_track.stop()    
            # Detach the output track queue from the program, let the clean up to GC
            output_track.purge() 
            
            # Stop outgoing frames to frontend 
            output_track.stop()
            logger.info("Pipeline: Resources cleaned up.")
             
        except Exception as cleanup_err:
            logger.error(f"Error during pipeline cleanup: {cleanup_err}")

        # 3. Close WebRTC (This clears ICE, Transceivers, etc.)
        try:
            await pc.close()
            logger.info("Peer connection closed.")
        except Exception as e:
            logger.error(f"Failed to close peer connection: {e}")           

        # 4. Finalize the master root trace right before clearing memory allocations
        try:
            
            session_trace.update(
                output={
                    "closure_reason": final_closure_status,
                    "session_duration_seconds": time.time() - ctx.shared_data["peer_state"].get("connected_at", time.time())
                },
                # Pass your accumulated token usage counts back to the final metrics view
                metadata=ctx.shared_data.get("metadata", {})
            )
            session_trace.end()
            ctx.shared_data["resources"]["tracer"].flush()
        except Exception as trace_end_err:
            logger.error(f"Failed to cleanly commit final Langfuse trace state: {trace_end_err}")

    logger.info("Pipeline finished")

async def _process_video(track):
    """Process incoming video frames — pipe to vision model here."""
    frame_count = 0
    logger.info(f"Video processing started: {track.id[:8]}")

    while True:
        try:
            frame = await track.recv()
            frame_count += 1

            if frame_count == 1:
                logger.info(
                    f"First video frame — "
                    f"{frame.width}x{frame.height} "
                    f"format={frame.format.name}"
                )

            if frame_count % 100 == 0:
                logger.info(f"Video track {track.id[:8]}: {frame_count} frames")

            # ── TODO: pipe to vision pipeline ─────────────────────────────
            # await vision_pipeline.push(frame)

        except MediaStreamError:
            logger.info(f"Video track {track.id[:8]} ended — {frame_count} frames total")
            break

        except Exception as e:
            logger.error(f"Video track error: {e}")
            break


async def close_all_peers():
    """Call on server shutdown to clean up all connections."""
    logger.info(f"Closing {len(active_connections)} peer connections")
    await asyncio.gather(
        *[pc.close() for pc in list(active_connections)],
        return_exceptions=True
    )
    active_connections.clear()