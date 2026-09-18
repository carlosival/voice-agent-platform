'''
A tiny wrapper around the call to the STT API
'''

import httpx
from typing import AsyncGenerator, Optional
from yaafpy.types import ExecContext
from workflows.signals import EndOfStream
from ..utils import call_stt_openai
from av import AudioFrame
import os
import numpy as np

STT_BASE_URL = os.getenv("STT_BASE_URL",  "http://speaches:8000")
STT_MODEL    = os.getenv("STT_MODEL",     "Systran/faster-whisper-large-v3")
STT_LANGUAGE = os.getenv("STT_LANGUAGE",  "es")
STT_API_KEY  = os.getenv("STT_API_KEY")


def frames_to_pcm_s16le(frames: list[AudioFrame]) -> bytes:
    """
    Convert Silero VAD AudioFrames:
        float32, mono, 16 kHz
    into:
        PCM signed 16-bit little-endian, mono, 16 kHz
    """

    pcm_chunks = []

    for frame in frames:
        # frame.planes[0] contains raw float32 PCM
        samples = np.frombuffer(
            frame.planes[0],
            dtype=np.float32,
        )

        # float32 [-1.0, 1.0] -> int16 [-32768, 32767]
        samples = np.clip(samples, -1.0, 1.0)

        pcm_s16 = (samples * 32767.0).astype(np.int16)

        pcm_chunks.append(pcm_s16.tobytes())

    return b"".join(pcm_chunks)

async def call_stt(frames: list[AudioFrame], model: str = STT_MODEL, language: str = STT_LANGUAGE, api_key: str = STT_API_KEY, base_url: str = STT_BASE_URL, http_client: Optional[httpx.AsyncClient] = None) -> str:
    # Flatten the list of AudioFrame objects into one PCM byte string
    # Silero frames:
    # float32 / mono / 16 kHz
    #
    # Convert to:
    # int16 / mono / 16 kHz
    pcm_s16le_bytes = frames_to_pcm_s16le(frames)
    return await call_stt_openai(pcm_s16le_bytes, model, language, api_key, base_url, http_client)