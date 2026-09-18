from workflows.steps.vad.types import RefBool
from aiortc.mediastreams import MediaStreamError
from typing import AsyncGenerator
import asyncio
from workflows.signals import WarmUp, AskUserStillThere, StartSpeaking, EndOfStream

import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

'''
Consume utterance from VAD and keep in a queue (backpressure)
'''
async def vad_backpressure(source: AsyncGenerator, queue: asyncio.Queue, is_closing: RefBool):
        try:
            async for item in source:
                if isinstance(item, WarmUp):
                    logger.info("Bridge: Warmup signal received.")
                if isinstance(item, AskUserStillThere):
                    logger.info("Bridge: User still there signal received.")
                    #utterance queue should be empty
                    if queue.empty():
                        logger.info("Bridge: Utterance queue is empty.")
                    else:
                        logger.warning("Bridge: Utterance queue is not empty.")
                if isinstance(item, StartSpeaking):
                    # Clear the queue so Turn 2 doesn't sit behind Turn 1's leftovers
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    logger.info("Bridge: Purged track and queue on StartSpeaking")
                
                if isinstance(item, EndOfStream):
                    logger.info("Bridge: Received EndOfStream, shutting down.")
                    await asyncio.wait_for(queue.put(item), timeout=2.0)
                    return # Exit the harvester loop
                try:
                    await asyncio.wait_for(queue.put(item), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("Bridge: Queue full and blocked for too long. Dropping frame.")  
        except (asyncio.CancelledError, GeneratorExit, MediaStreamError):
            is_closing.value = True
        except Exception as e:
            logger.error("Bridge Harvester Error", exc_info=True)
        finally:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait() 
                    queue.put_nowait(None)
                except Exception:
                    logger.error("Bridge: Failed to send None sentinel to queue.")