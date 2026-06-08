import os
import asyncio
import io
import wave
import numpy as np
import httpx
from httpx import AsyncClient
from typing import AsyncGenerator
from av import AudioFrame
from .helpers import pcm_to_wav, frames_to_pcm

STT_BASE_URL = os.getenv("STT_BASE_URL",  "http://speaches:8000")
STT_MODEL    = os.getenv("STT_MODEL",     "Systran/faster-whisper-large-v3")
STT_LANGUAGE = os.getenv("STT_LANGUAGE",  "es")
STT_API_KEY  = os.getenv("STT_API_KEY")
SAMPLE_RATE  = 48000


# ─── Helpers ──────────────────────────────────────────────────────────────────

def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw PCM int16 bytes in a WAV container — Whisper needs a file format."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)           # mono
        wf.setsampwidth(2)           # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def frames_to_pcm(frames: list[np.ndarray]) -> bytes:
    """Concatenate aiortc AudioFrames into raw PCM bytes."""
    arrays = [np.array(f.to_ndarray(), dtype=np.int16).flatten() for f in frames]
    return np.concatenate(arrays).tobytes()


# ─── STT Call — full buffer ───────────────────────────────────────────────────

async def call_stt_openai(http_client: AsyncClient, audio: bytes, is_wav: bool = False) -> str:
    """
    Transcribe audio bytes via Groq's OpenAI-compatible API.
    """
    wav_bytes = audio if is_wav else pcm_to_wav(audio)

    # 1. Groq requires an Authorization header with your API key
    headers = {
        "Authorization": f"Bearer {STT_API_KEY}"
    }

    # 2. Adjusted URL pathing based on standard Groq/OpenAI structure
    # If STT_BASE_URL is "https://api.groq.com/openai", use "/v1/audio/transcriptions"
    url = f"{STT_BASE_URL}/v1/audio/transcriptions"

    resp = await http_client.post(
        url,
        headers=headers,  # ← Added headers
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data={
            "model":    STT_MODEL,     # ← Make sure this is "whisper-large-v3-turbo"
            "language": STT_LANGUAGE,
            "response_format": "json",
            "temperature": "0",
            # 3. REMOVED "no_speech_threshold". Groq does not support this parameter 
            # and will throw an error if you pass it.
        },
        timeout=10.0, # Groq is very fast, but 10s gives a buffer for file uploads over WAN
    )
    resp.raise_for_status()
    return resp.json()["text"].strip()


async def call_stt_speaches(http_client: AsyncClient, audio: bytes, is_wav: bool = False) -> str:
    """
    Transcribe audio bytes via Speaches /v1/audio/transcriptions.

    Args:
        audio:   raw PCM bytes OR WAV bytes
        is_wav:  True if audio is already a WAV file, False if raw PCM

    Returns:
        Transcribed text string.
    """
    wav_bytes = audio if is_wav else pcm_to_wav(audio)

    resp = await http_client.post(
        f"{STT_BASE_URL}/v1/audio/transcriptions",
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data={
            "model":    STT_MODEL,
            "language": STT_LANGUAGE,
            "response_format": "json",
            "no_speech_threshold": "0.6",   # ← reject if Whisper thinks it's silence
            "temperature": "0",             # ← deterministic, less hallucination
        },
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()["text"].strip()


# ─── STT Call — from aiortc frames ───────────────────────────────────────────

async def call_stt_from_frames_speaches(http_client: AsyncClient, frames: list[AudioFrame]) -> str:
    """
    Transcribe directly from a list of aiortc AudioFrames.
    Use this in your _process_audio() loop.
    """
    pcm = frames_to_pcm(frames)
    return await call_stt_speaches(http_client, pcm, is_wav=False)

async def call_stt_from_frames_openai(http_client: AsyncClient, frames: list[AudioFrame]) -> str:
    """
    Transcribe directly from a list of aiortc AudioFrames.
    Use this in your _process_audio() loop.
    """
    pcm = frames_to_pcm(frames)
    return await call_stt_openai(http_client, pcm, is_wav=False)


# ─── STT Call — streaming (SSE) ───────────────────────────────────────────────

async def call_stt_stream(
    http_client: AsyncClient,
    audio: bytes,
    is_wav: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Stream transcription chunks via SSE as Whisper processes audio.
    Yields partial text as it arrives — don't wait for full transcription.
    """
    wav_bytes = audio if is_wav else pcm_to_wav(audio)

    async with http_client.stream(
        "POST",
        f"{STT_BASE_URL}/v1/audio/transcriptions",
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data={
            "model":           STT_MODEL,
            "language":        STT_LANGUAGE,
            "response_format": "json",
            "stream":          "true",       # enables SSE streaming
        },
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload and payload != "[DONE]":
                    import json
                    try:
                        chunk = json.loads(payload)
                        text  = chunk.get("text", "")
                        if text:
                            yield text
                    except json.JSONDecodeError:
                        continue


# ─── Test ─────────────────────────────────────────────────────────────────────
# Uses tis command to test:
# docker exec -it -e STT_BASE_URL="http://speaches:8000" -e STT_MODEL="Systran/faster-whisper-large-v3" -e STT_LANGUAGE="es" fastapi python3 -m utils.speech_to_text

async def _test():
    print(f"URL:      {STT_BASE_URL}")
    print(f"Model:    {STT_MODEL}")
    print(f"Language: {STT_LANGUAGE}\n")

    # Generate a 2-second 440Hz sine wave as fake "speech" for testing
    t        = np.linspace(0, 2, SAMPLE_RATE * 2)
    pcm      = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    pcm_bytes = pcm.tobytes()

    async with httpx.AsyncClient(timeout=30.0) as client:

        # Test 1 — full transcription from raw PCM
        print("── Test 1: Full transcription (PCM) ──")
        text = await call_stt(client, pcm_bytes, is_wav=False)
        print(f"Result: '{text}'\n")

        # Test 2 — full transcription from WAV
        print("── Test 2: Full transcription (WAV) ──")
        wav   = pcm_to_wav(pcm_bytes)
        text  = await call_stt(client, wav, is_wav=True)
        print(f"Result: '{text}'\n")

        # Test 3 — streaming transcription
        print("── Test 3: Streaming transcription ──")
        async for chunk in call_stt_stream(client, pcm_bytes):
            print(f"  Chunk: '{chunk}'")
        print()

        # Test 4 — from a real WAV file (if available)
        if os.path.exists("test_transcribe.wav"):
            print("── Test 4: From test_transcribe.wav ──")
            wav_bytes = open("test_transcribe.wav", "rb").read()
            text      = await call_stt(client, wav_bytes, is_wav=True)
            print(f"Result: '{text}'\n")


if __name__ == "__main__":
    asyncio.run(_test())