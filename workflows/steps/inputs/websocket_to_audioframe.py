# ──────────────────────────────────────────────
# Stage 0 (Telnyx variant) — async generator wrapping the WS stream
# ──────────────────────────────────────────────
import asyncio
import base64
import json

from asyncio import TimeoutError, CancelledError, wait_for
from typing import AsyncGenerator, Optional

import av
import numpy as np
from av import AudioFrame
from av.audio.resampler import AudioResampler
from websockets.exceptions import ConnectionClosed

import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 48000
TARGET_FORMAT = "s16"
TARGET_LAYOUT = "mono"

# Telnyx `media_format.encoding` -> PyAV raw-PCM decoder name
# (None means "already linear PCM, no decode needed")
_CODEC_MAP = {
    "PCMU": "pcm_mulaw",
    "PCMA": "pcm_alaw",
    "L16": None,
    "PCM16": None,
}


async def telnyx_track_frames(websocket) -> AsyncGenerator[AudioFrame, None]:
    """
    Wraps a Telnyx Media Streaming websocket connection (the `websockets`
    lib connection object, or anything with `async .recv()`), decodes the
    negotiated codec, resamples to 48kHz mono s16, and yields AudioFrame
    objects — drop-in replacement source for track_frames().
    """
    frame_count = 0
    wait_for_timeout = 120

    decoder: Optional[av.CodecContext] = None
    resampler: Optional[AudioResampler] = None
    source_sample_rate = 8000
    stream_id = None

    try:
        while True:
            try:
                raw = await wait_for(websocket.recv(), timeout=wait_for_timeout)
            except TimeoutError:
                logger.info("Telnyx WS: No message for 120 seconds, connection is likely dead")
                break
            except CancelledError:
                logger.info("Telnyx WS: Cancelled.")
                break
            except ConnectionClosed:
                logger.info("Telnyx WS: Connection closed")
                break

            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue

            event = msg.get("event")

            if event == "connected":
                logger.info("Telnyx WS: connected")
                continue

            if event == "start":
                start = msg["start"]
                stream_id = start.get("stream_id")
                media_format = start.get("media_format", {})
                encoding = media_format.get("encoding", "PCMU").upper()
                source_sample_rate = media_format.get("sample_rate", 8000)
                channels = media_format.get("channels", 1)

                logger.info(
                    f"Telnyx WS: stream started | stream_id={stream_id} "
                    f"encoding={encoding} sample_rate={source_sample_rate} channels={channels}"
                )

                codec_name = _CODEC_MAP.get(encoding)
                if codec_name:
                    decoder = av.CodecContext.create(codec_name, "r")
                    decoder.sample_rate = source_sample_rate
                    decoder.layout = "mono" if channels == 1 else "stereo"
                else:
                    decoder = None  # L16 / PCM16 already linear

                resampler = AudioResampler(
                    format=TARGET_FORMAT, layout=TARGET_LAYOUT, rate=TARGET_SAMPLE_RATE
                )
                continue

            if event == "media":
                media = msg["media"]
                # if bidirectionalMode/track=both_tracks, only take the caller's audio
                if media.get("track") not in (None, "inbound"):
                    continue

                payload = base64.b64decode(media["payload"])

                if decoder is not None:
                    decoded_frames = decoder.decode(av.Packet(payload))
                else:
                    # L16 payload is big-endian per RTP spec (RFC 3551)
                    samples = np.frombuffer(payload, dtype=">i2").astype("<i2")
                    frame = AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
                    frame.sample_rate = source_sample_rate
                    decoded_frames = [frame]

                for src_frame in decoded_frames:
                    src_frame.sample_rate = source_sample_rate
                    out_frames = resampler.resample(src_frame)
                    if out_frames is None:
                        continue
                    if not isinstance(out_frames, list):
                        out_frames = [out_frames]

                    for frame in out_frames:
                        frame_count += 1

                        if frame_count <= 3:
                            arr = frame.to_ndarray()
                            logger.info(
                                f"Frame #{frame_count} | "
                                f"format={frame.format.name} | "
                                f"layout={frame.layout.name} | "
                                f"sample_rate={frame.sample_rate} | "
                                f"samples={frame.samples} | "
                                f"shape={arr.shape} | "
                                f"dtype={arr.dtype} | "
                                f"min={arr.min()} max={arr.max()} "
                                f"rms={np.sqrt(np.mean(arr.astype(np.float32)**2)):.1f}"
                            )

                        if frame_count % 100 == 0:
                            logger.info(f"Telnyx track received frame {frame_count}")

                        yield frame
                continue

            if event == "stop":
                logger.info(f"Telnyx WS: stream stopped | stream_id={stream_id}")
                break

            # 'mark', 'dtmf', etc — ignore
    finally:
        logger.info("Closing Telnyx WS track")