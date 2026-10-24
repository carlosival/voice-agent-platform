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
        float, mono, 16 kHz
    into:
        PCM signed 16-bit little-endian, mono, 16 kHz
    """
    pcm_chunks = []

    for frame in frames:
        # to_ndarray() respeta estrictamente frame.samples y descarta el padding
        # Para audio mono, devuelve un array de forma (1, samples) o (samples,)
        samples = frame.to_ndarray()
        
        # A veces viene con forma (1, N), lo aplanamos a 1D por seguridad
        samples = samples.flatten()

        # Limpiamos por si acaso hay algún NaN remanente
        samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)

        # float32 [-1.0, 1.0] -> int16 [-32768, 32767]
        samples = np.clip(samples, -1.0, 1.0)
        pcm_s16 = (samples * 32767.0).astype(np.int16)

        pcm_chunks.append(pcm_s16.tobytes())

    return b"".join(pcm_chunks)


def pcm_f32le_to_pcm_s16le(pcm: np.ndarray) -> bytes:
    """
    Convert mono audio from:
        float32 [-1.0, 1.0]
    to:
        PCM signed 16-bit little-endian (S16LE).

    Args:
        pcm: NumPy array containing float32 audio samples.

    Returns:
        Raw PCM S16LE bytes.
    """
    samples = np.asarray(pcm, dtype=np.float32).flatten()

    # Replace invalid values
    samples = np.nan_to_num(
        samples,
        nan=0.0,
        posinf=1.0,
        neginf=-1.0,
    )

    # Clamp samples to the valid audio range
    samples = np.clip(samples, -1.0, 1.0)

    # Convert float32 to signed 16-bit PCM
    pcm_s16 = (samples * 32767.0).astype("<i2")

    return pcm_s16.tobytes()

async def call_stt( utterance_pcm_f32le: np.ndarray, 
                    model: str = STT_MODEL, 
                    provider:str ="unknow", 
                    language: str = STT_LANGUAGE, 
                    api_key: str = STT_API_KEY, 
                    base_url: str = STT_BASE_URL, 
                    http_client: Optional[httpx.AsyncClient] = None, 
                    tracing_data: dict = {}) -> str:
    """
    Transcribe an utterance detected by Silero VAD.

    Args:
        utterance_pcm_f32le: Concatenated mono float32 audio samples
            belonging to a single utterance, normalized to [-1.0, 1.0].

    Returns:
        Transcribed text.
    """
    pcm_s16le_bytes = pcm_f32le_to_pcm_s16le(utterance_pcm_f32le)

   
    return await call_stt_openai(audio=pcm_s16le_bytes, 
                                model=model, 
                                provider=provider, 
                                language=language, 
                                api_key=api_key, 
                                base_url=base_url, 
                                http_client=http_client, 
                                tracing_data=tracing_data)