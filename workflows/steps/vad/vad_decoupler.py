from .types import RefBool
from yaafpy.types import ExecContext
from typing import AsyncGenerator
from .workers.backpressure_worker import vad_backpressure
from workflows.signals import EndOfStream
from .config import QUEUE_BACKPRESSURE_MAXSIZE

import logging
import asyncio

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 1.5  –  VAD Decoupler applying backpressure
# list[AudioFrame]  →  list[AudioFrame]
# ════════════════════════════════════════════════════════════════════════════════

async def brain_bridge(source: AsyncGenerator, ctx: ExecContext) -> AsyncGenerator:
    
    queue = asyncio.Queue(maxsize=QUEUE_BACKPRESSURE_MAXSIZE)
    
    # Track if we are already shutting down to avoid double-closing
    is_closing = RefBool(False)

    harvester_task = asyncio.create_task(vad_backpressure(source, queue, is_closing))

    try:
        while True:
            item = await queue.get()
            if isinstance(item, EndOfStream):
                yield item
                break
            if item is None:
                break
            yield item
    finally:
        # CLEANUP WITHOUT THE RUNTIME ERROR:
        # We check if the harvester is already done before trying to kill it
        if not harvester_task.done():
            harvester_task.cancel()
            try:
                # Shield the cleanup so it doesn't conflict with yaafpy's internal aclose()
                await harvester_task 
            except asyncio.CancelledError:
                pass
