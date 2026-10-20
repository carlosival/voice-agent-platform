import os, logging, time, uuid, asyncio, io, wave, httpx
import numpy as np
from httpx import AsyncClient
from typing import AsyncGenerator
from av import AudioFrame
from .helpers import pcm_to_wav, frames_to_pcm, save_pcm_s16le_wav
from pathlib import Path


from openinference.semconv.trace import (
    SpanAttributes,
    AudioAttributes,
    OpenInferenceSpanKindValues,
    OpenInferenceMimeTypeValues,
)

from opentelemetry.trace import Status, StatusCode, set_span_in_context
from opentelemetry.context import attach, detach, get_current

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STT_BASE_URL = os.getenv("STT_BASE_URL",  "http://speaches:8000")
STT_MODEL    = os.getenv("STT_MODEL",     "Systran/faster-whisper-large-v3")
STT_LANGUAGE = os.getenv("STT_LANGUAGE",  "es")
STT_API_KEY  = os.getenv("STT_API_KEY")
STORAGE_PATH = Path(os.getenv("AUDIO_STORAGE_PATH"))
SAMPLE_RATE  = 48000

WAV_MIME = "audio/wav"

# Custom attributes have no semconv constant, so keep them in one place
class STTAttributes:
    KIND =  "stt"
    LANGUAGE = "stt.language"
    COST = "stt.cost"
    MODEL ="stt.model"
    PROVIDER = "stt.provider"
    AUDIO_BYTES = "stt.audio.bytes"
    AUDIO_DURATION_S = "stt.audio.duration_s"
    TEXT_CHARS = "stt.text.chars"
    HTTP_STATUS = "stt.http.status_code"
    HTTP_LATENCY_MS = "stt.http.latency.ms"
    PROVIDER_REAL_TIME_FACTOR = "stt.rtf"


# ─── STT Call — full buffer ───────────────────────────────────────────────────

async def call_stt_openai(
    audio: bytes, 
    model: str = STT_MODEL, 
    provider:str = "unknow",
    language: str = STT_LANGUAGE, 
    api_key: str = STT_API_KEY, 
    base_url: str = STT_BASE_URL, 
    http_client: AsyncClient = None, 
    tracing_data: dict = {}) -> str:
    """
    Transcribe audio bytes via Groq's OpenAI-compatible API.
    `audio` must be:
        PCM S16LE 16 kHz mono (bytes), unless `is_wav=True`.
    """
    
    tracer = tracing_data["tracer"]
    parent_turn = tracing_data["parent_span"]
    parent_ctx = set_span_in_context(parent_turn)
    
    key = uuid.uuid4()
    # Path /audio/uuid_key.wav inside docker container
    path = STORAGE_PATH / f"{str(key)}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save audio input to disk without blocking the event loop
    await asyncio.to_thread(
        save_pcm_s16le_wav,
        audio,
        str(path),
    )
    
    with tracer.start_as_current_span("stt.transcribe", context=parent_ctx) as span:
        span.set_attribute(
            SpanAttributes.OPENINFERENCE_SPAN_KIND,
            OpenInferenceSpanKindValues.CHAIN.value,
        )

        span.set_attribute(SpanAttributes.INPUT_VALUE, path)
        span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, WAV_MIME)
        
        span.set_attribute(STTAttributes.MODEL, model)
        span.set_attribute(STTAttributes.PROVIDER, provider)
        
        span.set_attribute(AudioAttributes.AUDIO_MIME_TYPE, WAV_MIME)
        span.set_attribute(AudioAttributes.AUDIO_URL, key)

        span.set_attribute(STTAttributes.LANGUAGE, language)
        

        try:
            wav_bytes = pcm_to_wav(audio, sample_rate=16000)

            # 1. Groq, OpenAI, Speaches, etc. requires an Authorization header with your API key
            headers = {
                "Authorization": f"Bearer {api_key}"
            }

            # 2. Adjusted URL pathing based on standard Groq/OpenAI structure
            # If STT_BASE_URL is "https://api.groq.com/openai", use "/v1/audio/transcriptions"
            url = f"{base_url}/v1/audio/transcriptions"

            start = time.perf_counter()
            resp = await http_client.post(
                url,
                headers=headers,  # ← Added headers
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={
                    "model":    model,     # ← Make sure this is "whisper-large-v3-turbo"
                    "language": language,
                    "response_format": "json",
                    "temperature": "0",
                    # 3. REMOVED "no_speech_threshold". Groq does not support this parameter 
                    # and will throw an error if you pass it.
                },
                timeout=10.0, # Groq is very fast, but 10s gives a buffer for file uploads over WAN
            )
            request_latency_ms = (time.perf_counter() - start) * 1000
            resp.raise_for_status()
            text = resp.json()["text"].strip()
            audio_duration = len(audio) / (16000 * 1 * 2)
            span.set_attribute(STTAttributes.HTTP_LATENCY_MS, request_latency_ms)
            span.set_attribute(STTAttributes.HTTP_STATUS, resp.status_code)
            span.set_attribute(STTAttributes.AUDIO_BYTES, len(wav_bytes))
            span.set_attribute(STTAttributes.AUDIO_DURATION_S, audio_duration)
            span.set_attribute(STTAttributes.PROVIDER_REAL_TIME_FACTOR, round((request_latency_ms/audio_duration),3))
            span.set_attribute(SpanAttributes.OUTPUT_VALUE, text)
            span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, OpenInferenceMimeTypeValues.TEXT.value)
            span.set_attribute(AudioAttributes.AUDIO_TRANSCRIPT, text)
            span.set_attribute(STTAttributes.COST, 0)
            span.set_attribute(STTAttributes.TEXT_CHARS, len(text))
            span.set_status(Status(StatusCode.OK))

            return text
        
        except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

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

async def call_stt_from_frames_openai(frames: list[AudioFrame], model: str = STT_MODEL, language: str = STT_LANGUAGE, api_key: str = STT_API_KEY, base_url: str = STT_BASE_URL, http_client: AsyncClient = None) -> str:
    """
    Transcribe directly from a list of aiortc AudioFrames.
    Use this in your _process_audio() loop.
    """
    pcm = frames_to_pcm(frames)
    return await call_stt_openai(pcm, model, language, api_key, base_url, http_client, is_wav=False)


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