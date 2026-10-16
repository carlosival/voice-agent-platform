from workflows import VOICE_WORKFLOW, track_frames
from opentelemetry.trace import Status, StatusCode
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
    Fix This
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
    pc = ctx.shared_data["resources"]["pc"]
    conversation_span = ctx.shared_data["trace_context"]["conversation_span"]
    final_closure_status = "completed_normally"

    try:
        async for _ in VOICE_WORKFLOW.run(source, ctx):
            pass
    
    # Place to add post pipeline HOOKS, could be async or sync
    # can be add at runtime in ctx.shared_data["hooks"]["post_pipeline_hooks"]
    # TODO: Implement this

    except asyncio.CancelledError as e:
        logger.info("Pipeline cancelled (Normal shutdown or client disconnect)")
        final_closure_status = "client_disconnect_or_barge_in_shutdown"
        # Log the interruption info immediately to the trace instance

        conversation_span.record_exception(e)
        conversation_span.set_status(Status(StatusCode.OK, str(e)))
        
        
    except httpx.ReadTimeout as e:
        logger.error("Pipeline Error: External API timed out (STT/LLM/TTS).")
        # Optional: You could send a message to the UI here via the websocket
        final_closure_status = "api_timeout_failure"
        
        conversation_span.record_exception(e)
        conversation_span.set_status(Status(StatusCode.ERROR, f"Network Timeout Encountered: {str(e)}"))
         
    except Exception as e:
        # This catches everything else to prevent the 'Task exception was never retrieved' error
        logger.error(f"Pipeline Critical Failure: {type(e).__name__} - {e}", exc_info=True)
        final_closure_status = "critical_pipeline_crash"
        
        conversation_span.record_exception(e)
        conversation_span.set_status(Status(StatusCode.ERROR, f"Pipeline crashed with uncaught error: {type(e).__name__} - {str(e)}"))
    
    finally:

        # 1. Ensure the output track is cleared and resources are freed
        try:    
            # Stop MediaStreamTrack incoming frames
            input_track.stop()    
            # Detach the output track queue from the program, let the clean up to GC
            output_track.clear() 
            
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
              
            if final_closure_status == "completed_normally":
                conversation_span.set_status(Status(StatusCode.OK))
            
            conversation_span.end()
            ctx.shared_data["resources"]["tracer_provider"].force_flush(timeout_millis=10_000)
        except Exception as trace_end_err:
            logger.error(f"Failed to cleanly commit final Observability trace state: {trace_end_err}")

    logger.info("Pipeline finished")