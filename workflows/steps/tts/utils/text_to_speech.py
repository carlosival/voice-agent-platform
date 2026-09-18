import os
import asyncio
import uuid
import httpx
from pathlib import Path
from typing import AsyncGenerator, Optional, Any
import wave
import io
import scipy.signal as signal
import numpy as np
import logging
from workflows.utils.helpers import save_debug_wav
from dataclasses import dataclass



TTS_API_KEY = os.getenv("TTS_API_KEY", "dummy_key")

logger = logging.getLogger(__name__)

TTS_MODEL    = os.getenv("TTS_MODEL",    "speaches-ai/piper-es_ES-sharvard-medium")
TTS_LANGUAGE = os.getenv("TTS_LANGUAGE", "es-ES")
TTS_VOICE    = os.getenv("TTS_VOICE",    "sharvard")
TTS_BASE_URL     = os.getenv("TTS_BASE_URL",     "http://speaches:8000")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")
STATIC_DIR   = Path(os.getenv("STATIC_DIR", "./static"))
SAMPLE_RATE  = 48000
SAMPLES_PER_FRAME = 960            # 20ms at 48kHz
BYTES_PER_FRAME   = SAMPLES_PER_FRAME * 2  # int16 = 2 bytes per sample = 1920 bytes
WAV_MIN_HEADER_PROBE = 12          # 'RIFF'(4) + size(4) + 'WAVE'(4)
MAX_HEADER_SEARCH_BYTES = 1 << 16  # 64 KB safety cap while hunting for 'data'

@dataclass
class WavFmt:
    channels: int
    sample_rate: int
    bits_per_sample: int

def _find_wav_data(buf: bytes) -> tuple[Optional[int], Optional[WavFmt]]:
    pos = 12
    fmt = None
    while pos + 8 <= len(buf):
        chunk_id = buf[pos:pos + 4]
        size = struct.unpack("<I", buf[pos + 4:pos + 8])[0]
        body = pos + 8

        if chunk_id == b"fmt " and body + size <= len(buf):
            _audio_format, channels, sample_rate = struct.unpack("<HHI", buf[body:body + 8])
            bits_per_sample = struct.unpack("<H", buf[body + 14:body + 16])[0]
            fmt = WavFmt(channels, sample_rate, bits_per_sample)
        elif chunk_id == b"data":
            return body, fmt

        pos = body + size + (size & 1)
    return None, fmt

def _validate_wav_fmt(fmt: WavFmt, sample_rate: int, strict: bool = False) -> None:
    """
    Compare a parsed WAV 'fmt ' chunk against the AudioFormat the caller
    configured as input_format. Raises WavFormatMismatch on disagreement.

    strict=False downgrades to a warning-friendly caller (you decide what
    to do with the returned issues) instead of raising — useful if you'd
    rather clamp/adapt than hard-fail mid-stream.
    """
    issues = []


    if fmt.sample_rate != sample_rate:
        issues.append(
            f"sample rate mismatch: WAV declares {fmt.sample_rate} Hz, "
            f"expected {sample_rate} Hz"
        )

        logger.warning(
            f"sample rate mismatch: WAV declares {fmt.sample_rate} Hz, "
            f"expected {sample_rate} Hz"
        )


    if issues and strict:
        raise Exception(
            "TTS provider's WAV header does not match configured input_format: "
            + "; ".join(issues)
        )

# ─── TTS Call ─────────────────────────────────────────────────────────────────

async def call_tts( text: str, provider: str, tts_model: str, tts_voice: str, tts_base_url: str, tts_api_key: str, response_format: str, sample_rate: int, http_client: httpx.AsyncClient,) -> Any:
    """
    Call Speaches TTS endpoint (OpenAI-compatible /v1/audio/speech).
    Returns raw WAV bytes — caller decides what to do with them.
    """
    payload = make_payload(text, provider, tts_model, tts_voice, response_format, sample_rate)
    
    resp = await http_client.post(
            f"{TTS_BASE_URL}/v1/audio/speech",
            json=payload,
    )
    resp.raise_for_status()
    return resp.content                         # ✅ AudioChunk


async def call_tts_save(http_client: httpx.AsyncClient, text: str) -> str:
    """
    Call TTS and save to ./static/ — returns public URL.
    Use this for HTTP endpoints that serve audio files.
    """
    wav_bytes = await call_tts(http_client, text)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    filename  = f"{uuid.uuid4().hex}.wav"
    path      = STATIC_DIR / filename
    path.write_bytes(wav_bytes)

    #Can be save also to cloud storage and share the url with a limit access time

    return f"{BASE_URL}/static/{filename}"      # e.g. http://localhost:8080/static/abc123.wav


# ─── TTS Call — streaming ─────────────────────────────────────────────────────

async def call_tts_stream(
    text: str,
    payload: dict,
    base_url: str = TTS_BASE_URL,
    api_key: str = TTS_API_KEY,
    http_client: httpx.AsyncClient = None,
    debug: bool = False
) -> AsyncGenerator[bytes, None]:
    
    """
    Streams raw PCM audio. 
    Explicitly handles the difference between WAV (with header) and PCM (raw).
    """
    buffer = b""
    header_stripped = False
    debug_pcm = bytearray() if debug else None

    # Ensure debug directory exists
    if debug:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
        print(f"\n── Test 4 Debug Mode Enabled ──")

    # 1. Speaches, Groq, OpenAI, etc. requires an Authorization header with your API key
    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    # API Request
    # Note: Use response_format="pcm" for raw data to avoid manual header stripping
    async with http_client.stream(
        "POST",
        f"{base_url}/v1/audio/speech",
        headers=headers,  # ← Added headers
        json=payload,
        timeout=httpx.Timeout(timeout=None, connect=5.0)
    ) as resp:
        resp.raise_for_status()
        decided_wav = None
        async for chunk in resp.aiter_bytes():
            if not chunk:
                break

            if header_stripped:
                out = chunk
            else:
                buffer += chunk
                out = None

                if decided_wav is None and len(buffer) >= WAV_MIN_HEADER_PROBE:
                    decided_wav = (
                        buffer[0:4] == b"RIFF"
                        and buffer[8:12] == b"WAVE"
                    )
                    if decided_wav is False:
                        out = buffer
                        buffer = b""
                        header_stripped = True

                if decided_wav and not header_stripped:
                    if len(buffer) > MAX_HEADER_SEARCH_BYTES:
                        raise ValueError(
                            "Could not locate 'data' subchunk within "
                            f"{MAX_HEADER_SEARCH_BYTES} bytes — malformed WAV header"
                        )
                    data_offset, fmt = _find_wav_data_offset(buffer)
                    if data_offset is not None:
                        if fmt is None:
                            raise ValueError(
                                "WAV stream reached 'data' subchunk without "
                                "a preceding 'fmt ' subchunk — cannot validate format"
                            )
                        _validate_wav_fmt(fmt, sample_rate)
                        out = buffer[data_offset:]
                        buffer = b""
                        header_stripped = True

                if not out:
                    continue

            if debug:
                debug_pcm.extend(out)

            yield out



# ─── Test ─────────────────────────────────────────────────────────────────────
# docker exec -it -e TTS_BASE_URL="http://speaches:8000" -e TTS_MODEL="speaches-ai/piper-es_ES-sharvard-medium" -e TTS_VOICE="sharvard" fastapi python3 -m utils.text_to_speech
 
async def _test():
    prompt = "Esto es una prueba de streaming del pipeline de texto a voz en español."
    print(f"URL:   {TTS_BASE_URL}")
    print(f"Input: {prompt}\n")
 
    async with httpx.AsyncClient(timeout=30.0) as client:
 
        # Test 1 — full bytes
        print("── Test 1: Full WAV bytes ──")
        wav = await call_tts(client, prompt)
        print(f"Received {len(wav)} bytes total\n")
 
        # Test 2 — save to file
        print("── Test 2: Save to file ──")
        url = await call_tts_save(client, prompt)
        print(f"Saved: {url}\n")
 
        # Test 3 — streaming (raw chunk log)
        print("── Test 3: Streaming chunks ──")
        total  = 0
        chunks = 0
        async for chunk in call_tts_stream(client, prompt, debug=True):
            total  += len(chunk)
            chunks += 1
            print(f"  Chunk {chunks}: {len(chunk)} bytes  (total so far: {total})")
        print(f"Done — {chunks} chunks, {total} bytes total\n")
 
        # Test 4 — streaming with debug flag
        print("── Test 4: Streaming with debug=True ──")
        async for _ in call_tts_stream(client, prompt, debug=True):
            pass  # frames still yielded normally; debug output is a side-effect
 
 
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_test())