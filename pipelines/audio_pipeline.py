from workflows import VOICE_WORKFLOW, track_frames
from yaafpy import ExecContext
import logging
import asyncio
import httpx
import time

logger = logging.getLogger(__name__)

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
    pc = ctx.shared_data["resources"]["pc"]
    
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