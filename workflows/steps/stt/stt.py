from .config import DEBUG, SAVE_TO_S3
from workflows.steps.stt.workers import debug_stt, save_utterance_s3
from workflows.steps.stt.workers import get_stt_provider, get_stt_provider_url
from yaafpy.types import ExecContext
from typing import AsyncGenerator
from workflows.signals import EndOfStream, AskUserStillThere, StartSpeaking, WarmUp 
import asyncio

import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cancel_all_tasks(tasks: list[asyncio.Task]):
    for task in tasks:
        if not task.done():
            task.cancel()
    tasks.clear()

# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 2  –  STT
# Reads audio frames from the source, utterance by utterance 
# and converts them to text using STT provider
# list[AudioFrame]  →  str
# ════════════════════════════════════════════════════════════════════════════════

async def stt_stream(
    source: AsyncGenerator,
    ctx:    ExecContext,
) -> AsyncGenerator:
    """
    Receives utterances, handles interruptions via task cancellation,
    and logs raw PCM to disk for debugging.
    """
    tasks = []
    
    http_client: httpx.AsyncClient = ctx.shared_data["resources"]["http_client"]
    stt_provider_name = ctx.shared_data["stt_config"]["engine"]
    logger.info(f"stt_provider_name:{stt_provider_name}")
    stt_model         = ctx.shared_data["stt_config"]["model"]
    stt_language      = ctx.shared_data["stt_config"]["language"]
    stt_api_key       = ctx.shared_data["stt_api_key"]
    stt_base_url = get_stt_provider_url(stt_provider_name)
    logger.info(f"stt_base_url:{stt_base_url}")

    current_task = None

    async for item in source:

        if isinstance(item, EndOfStream):
            cancel_all_tasks(tasks)
            yield item
            break

        # If we get a AskUserStillThere signal and task is done, yield it
        if isinstance(item, AskUserStillThere) and current_task and current_task.done():
            yield item
   
        # If we get a Stop signal, kill the Whisper task immediately
        if isinstance(item, StartSpeaking):
            cancel_all_tasks(tasks)
            yield item

        if isinstance(item, WarmUp):
            # We know audio is coming, but we ignore until UserFinished
            yield item

        if isinstance(item, list): # This is the actual AudioBuffer
            
            if DEBUG:
               asyncio.create_task(debug_stt(item))
            
            if SAVE_TO_S3:
                asyncio.create_task(save_s3(item))

            # Launch STT provider like Whisper as a task so we can cancel it if a StopAndClear arrives later
            # Refactor: Pass at runtime the provider provider url, api, model,language, etc
            stt_task = asyncio.create_task((get_stt_provider(stt_provider_name))(item, model=stt_model, language=stt_language, api_key=stt_api_key, base_url=stt_base_url, http_client=http_client))
            tasks.append(stt_task)

            try:
                transcript = await stt_task
                if transcript: yield transcript
            except asyncio.CancelledError:
                logger.info("STT: Task killed by StartSpeaking signal.")
            finally:
                # Cleanup task if it was orphaned by an error or cancellation
                cancel_all_tasks(tasks)