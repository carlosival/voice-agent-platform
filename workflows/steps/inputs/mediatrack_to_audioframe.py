from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from typing import AsyncGenerator
import asyncio
import av
from .config import WAIT_FOR_TIMEOUT
import numpy as np
import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Stage 0 — async generator wrapping the 
# MediaStreamTrack is the transport of the audio
# ──────────────────────────────────────────────
async def track_frames(track: MediaStreamTrack) -> AsyncGenerator[av.AudioFrame, None]:
    frame_count = 0
    resampler = av.AudioResampler(
            format="flt",
            layout="mono",
            rate=16000,
        )
    try:
        while True:
            try:
                frame = await asyncio.wait_for(track.recv(), timeout=WAIT_FOR_TIMEOUT)
                frame_count += 1

                if frame_count % 100 == 0:  # inspect mod 100 frames only
                    arr = frame.to_ndarray()
                    logger.info(
                        f"Frame #{frame_count} | "
                        f"format={frame.format.name} | "
                        f"layout={frame.layout.name} | "
                        f"channels (metadata): {frame.layout.nb_channels} | "
                        f"is_planar={frame.format.is_planar} | "
                        f"sample_rate={frame.sample_rate} | "
                        f"samples={frame.samples} | "
                        f"shape={arr.shape} | "
                        f"dtype={arr.dtype} | "
                        f"min={arr.min()} max={arr.max()} "
                        f"rms={np.sqrt(np.mean(arr.astype(np.float32)**2)):.1f}"
                )

                if frame_count % 100 == 0:
                    logger.info(f"Track received frame {frame_count}")
                
                # 48k stereo -> 16k mono
                output_frames = resampler.resample(frame)

                for output_frame in output_frames:
                    yield output_frame
            except asyncio.TimeoutError:
                logger.info("Track: No audio for 120 seconds, connection is likely dead")
                break    
            except asyncio.CancelledError:
                logger.info("Track: Cancelled.")
                break
            except (MediaStreamError, asyncio.TimeoutError):
                logger.info("Track: Connection lost")
                break
    finally:
        track.stop() 
        logger.info("Closing MediaStreamTrack") 