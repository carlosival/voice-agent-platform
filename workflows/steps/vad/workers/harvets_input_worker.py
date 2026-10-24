from aiortc.mediastreams import MediaStreamError
from typing import AsyncGenerator
import asyncio, logging

from workflows.signals import WarmUp, AskUserStillThere, StartSpeaking

from av import AudioFrame

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def vad_backpressure_input(source: AsyncGenerator, queue: asyncio.Queue):
    frame_count = 0
    try:
        async for frame in source:
                frame_count += 1
                if frame_count % 100 == 0:
                    logger.info(
                            f"Frame #{frame_count} | "
                            f"format={frame.format.name} | "
                            f"layout={frame.layout.name} | "
                            f"channels (metadata): {frame.layout.nb_channels} | "
                            f"is_planar={frame.format.is_planar} | "
                            f"sample_rate={frame.sample_rate} | "
                            f"samples={frame.samples} | "
                    )

                # ---- Defensive: make sure it's actually an audio frame ----
                if not isinstance(frame, AudioFrame):
                    logger.warning(f"VAD: expected AudioFrame, got {type(frame)!r}, skipping")
                    continue

                await queue.put(frame.to_ndarray())
    except (asyncio.CancelledError, GeneratorExit, MediaStreamError):
            logger.error("Input Harvester Error", exc_info=True)
            queue.put_nowait(None)
    except Exception as e:
        logger.error("Input Harvester Error", exc_info=True)
    finally:
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                queue.get_nowait() 
                queue.put_nowait(None)
            except Exception:
                logger.error("Input: Failed to send None sentinel to queue.")