import asyncio
import httpx
import logging
from typing import AsyncGenerator
from yaafpy.types import ExecContext, WorkflowAbortException
from yaafpy import StreamWorkflow
from workflows.steps.outputs.audio_output import IAudioOutput
from workflows.signals import SignalFrame, WarmUp, AskUserStillThere, EndOfStream, StartSpeaking
from .workers import get_tts_provider_worker
from .utils import get_tts_provider_url

from workflows.utils import (
    layered_has_speech,
    call_stt_from_frames_openai,
    call_stt_from_frames_speaches,
    call_llm_stream_openai,
    call_tts_stream,
    pcm_to_wav,
    frames_to_pcm,
    silero_has_speech_from_numpy,
    frames_to_mono_int16
)


import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 4  –  TTS
# str  →  bytes  (WAV blob per sentence)
# ════════════════════════════════════════════════════════════════════════════════

async def tts_stream(
    source: AsyncGenerator,
    ctx:    ExecContext,
) -> AsyncGenerator[bytes, None]:
    """
    Calls Speaches/Openai/etc compatible api model/voice per sentence chunk via call_tts_stream.
    Forwards raw PCM byte chunks as they arrive for minimal latency.
    Skips synthesis entirely if speaking_event was set between LLM chunks.
    """

    tasks= []
    http_client:    httpx.AsyncClient   = ctx.shared_data["resources"]["http_client"]
    output_track = ctx.shared_data["resources"]["output_track"]
    tts_provider_name = ctx.shared_data["tts_config"]["engine"]
    tts_model         = ctx.shared_data["tts_config"]["model"]
    tts_voice         = ctx.shared_data["tts_config"]["voice_id"]
    tts_language      = ctx.shared_data["tts_config"]["language"]
    tts_api_key       = ctx.shared_data["tts_api_key"]
    tts_base_url      = get_tts_provider_url(tts_provider_name)
    current_task = None
    tts_queue = asyncio.Queue()
    
    
    current_task = asyncio.create_task(
        get_tts_provider_worker(tts_provider_name)(
            http_client=http_client, 
            output_track=output_track, 
            tts_queue=tts_queue, 
            provider_name=tts_provider_name, 
            model=tts_model,
            voice=tts_voice,
            language=tts_language,
            api_key=tts_api_key,
            base_url=tts_base_url))

    try:
        async for sentence in source:
            if isinstance(sentence, WarmUp):
                logger.info(f"TTS received WarmUp signal.")
                await output_track.add_silence(duration_frames=50)
                await tts_queue.put("¡Hola! ¿Cómo puedo ayudarte?")

            if isinstance(sentence, EndOfStream):
                logger.info("TTS: EndOfStream received. Cleaning up.")
                # Optional: Send a goodbye message before killing
                await asyncio.wait_for(tts_queue.put("¡Hasta luego!"), timeout=2.0) 
                await asyncio.wait_for(tts_queue.put(None), timeout=2.0)
                break
            # 1. SIGNAL HANDLING (The "Kill Switch")
            if isinstance(sentence, StartSpeaking):
                logger.info("[AUDIO_KILL] TTS received StartSpeaking. Purging output track.")
                # Clear pending sentences
                while not tts_queue.empty():
                    tts_queue.get_nowait()
                # Cancel whatever is currently synthesizing
                if current_task and not current_task.done():
                    current_task.cancel()
                    try:
                        await current_task
                    except asyncio.CancelledError:
                        pass
                output_track.clear()
                # Restart the sequential worker
                current_task = asyncio.create_task(
                                                    get_tts_provider_worker(tts_provider_name)(
                                                        http_client=http_client, 
                                                        output_track=output_track, 
                                                        tts_queue=tts_queue, 
                                                        provider_name=tts_provider_name, 
                                                        model=tts_model,
                                                        voice=tts_voice,
                                                        language=tts_language,
                                                        api_key=tts_api_key,
                                                        base_url=tts_base_url))
                continue

            # 2. DATA HANDLING (The Sentence)
            if isinstance(sentence, str):
                logger.info(f"TTS received sentence: '{sentence}'")
                await asyncio.wait_for(tts_queue.put(sentence), timeout=2.0)
            
            # 3. ASK USER STILL THERE
            if isinstance(sentence, AskUserStillThere):
                logger.info("TTS received AskUserStillThere signal.")
                if tts_queue.empty():
                    await asyncio.wait_for(tts_queue.put("¿Te puedo ayudar en algo más?"), timeout=2.0)
            
    except Exception as e:
        logger.error(f"TTS Main Loop Exception: {e}", exc_info=True)
        raise
    finally:
        await asyncio.wait_for(tts_queue.put(None), timeout=2.0)  # shutdown sentinel
        if current_task and not current_task.done():
            current_task.cancel()
        # This is critical for yaafpy
        raise WorkflowAbortException("End of stream.")

    if False: yield  # ← makes Python treat this as an async generator function 

