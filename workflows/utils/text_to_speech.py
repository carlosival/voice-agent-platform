import os
import asyncio
import uuid
import httpx
from pathlib import Path
from typing import AsyncGenerator
import wave
import io
import scipy.signal as signal
import numpy as np
import logging
from .helpers import save_debug_wav


# Use the beta client as it includes the latest streaming and model capabilities
from google.cloud import texttospeech_v1 as texttospeech

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

# Initialize the async client once
tts_client = texttospeech.TextToSpeechAsyncClient()


async def _request_generator(text: str) -> AsyncGenerator[texttospeech.StreamingSynthesizeRequest, None]:
    """
    gRPC bidirectional streaming requires an input generator.
    The first message MUST contain the configuration parameters.
    Subsequent messages can feed raw text strings over time.
    """
    # 1. First message: Setup configurations
    config = texttospeech.StreamingSynthesizeConfig(
        voice=texttospeech.VoiceSelectionParams(
            language_code=TTS_LANGUAGE,
            name=TTS_VOICE,
            model_name=TTS_MODEL
        ),
        streaming_audio_config=texttospeech.StreamingAudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16, # Raw PCM
            sample_rate_hertz=SAMPLE_RATE
        )
    )
    yield texttospeech.StreamingSynthesizeRequest(streaming_config=config)

    # 2. Second message: Pass the text block or text chunk to synthesize
    # If feeding from a live LLM, you can loop and yield multiple text blocks here.
    input_text = texttospeech.StreamingText(
        text=text,
        prompt="Read aloud in a warm, welcoming tone."
    )
    yield texttospeech.StreamingSynthesizeRequest(input_text=input_text)


async def call_tts_stream_google(
    text: str,
    debug: bool = False,                                        
    debug_path: str = "./static/tts_debug"  
) -> AsyncGenerator[bytes, None]:
    """
    Streams raw PCM audio back in perfect 20ms WebRTC frames using 
    Google Cloud TTS's native bidirectional StreamingSynthesize gRPC pipeline.
    """
    buffer = b""
    debug_pcm = bytearray() if debug else None

    if debug:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
        print(f"\n── Google gRPC StreamingSynthesize Active ──")

    try:
        # Create the input gRPC stream sequence
        requests_stream = _request_generator(text)

        # Establish the bidirectional stream with Google
        response_stream = await tts_client.streaming_synthesize(requests_stream)

        # Process the fragments Google returns on the fly
        async for response in response_stream:
            # Check if there are valid audio bytes in this chunk
            chunk = response.audio_content
            if not chunk:
                continue

            buffer += chunk

            # Sift through the buffer to extract exact 20ms blocks
            while len(buffer) >= BYTES_PER_FRAME:
                frame = buffer[:BYTES_PER_FRAME]
                buffer = buffer[BYTES_PER_FRAME:]
                
                if debug:
                    debug_pcm.extend(frame)
                yield frame

        # Final Flush: Pad any remaining leftovers to keep the audio frame aligned
        if len(buffer) > 0:
            padding = BYTES_PER_FRAME - len(buffer)
            final_frame = buffer + (b"\x00" * padding)
            if debug:
                debug_pcm.extend(final_frame)
            yield final_frame

    except Exception as e:
        logger.error(f"Error during Google Cloud TTS Streaming: {e}")
        raise

    # --- Background Debug Task ---
    if debug and debug_pcm:
        unique_path = os.path.join(debug_path, f"tts_{uuid.uuid4().hex}.wav")
        Path(debug_path).mkdir(parents=True, exist_ok=True)
        
        asyncio.create_task(
            asyncio.to_thread(save_debug_wav, unique_path, debug_pcm, SAMPLE_RATE)
        )


# ─── TTS Call ─────────────────────────────────────────────────────────────────

async def call_tts(http_client: httpx.AsyncClient, text: str) -> bytes:
    """
    Call Speaches TTS endpoint (OpenAI-compatible /v1/audio/speech).
    Returns raw WAV bytes — caller decides what to do with them.
    """
 
    resp = await http_client.post(
        f"{TTS_BASE_URL}/v1/audio/speech",
        json={
            "model":           TTS_MODEL,
            "input":           text,
            "voice":           TTS_VOICE,
            "response_format": "wav",
            "sample_rate": SAMPLE_RATE
            
        },
    )
    resp.raise_for_status()
    return resp.content                         # ✅ raw bytes — push to WebRTC or save


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



async def call_tts_stream_google(
    http_client: httpx.AsyncClient,
    text: str,
    chunk_size: int = 4096, 
    debug: bool = False,                                        
    debug_path: str = "./static/tts_debug"  
) -> AsyncGenerator[bytes, None]:
    """
    Streams raw PCM audio chunks back in perfect 20ms WebRTC frames.
    Google Cloud Text-to-Speech does not support standard bidirectional HTTP streaming 
    for text inputs (synthesize_streaming is only available for long text via specific gRPC setups).
    However, we achieve identical internal streaming behavior by chunking Google's fast response 
    directly into your fixed 20ms (1920 byte) windows.
    """
    buffer = b""
    debug_pcm = bytearray() if debug else None

    if debug:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)

    # Fetch the full raw PCM bytes payload asynchronously
    raw_audio = await call_tts(text)
    
    # We read through the payload in incremental chunks to simulate the stream 
    # and feed your WebRTC buffer smoothly without modification downstream.
    for i in range(0, len(raw_audio), chunk_size):
        chunk = raw_audio[i:i + chunk_size]
        buffer += chunk

        # Yield chunks as soon as we have enough for a 20ms frame
        while len(buffer) >= BYTES_PER_FRAME:
            frame = buffer[:BYTES_PER_FRAME]
            buffer = buffer[BYTES_PER_FRAME:]
            
            if debug:
                debug_pcm.extend(frame)
            yield frame
            # Short yield pause to match network processing cadences if required
            await asyncio.sleep(0.001) 

    # Final Flush: If data is left over, pad it with silence to complete the frame
    if len(buffer) > 0:
        padding = BYTES_PER_FRAME - len(buffer)
        final_frame = buffer + (b"\x00" * padding)
        if debug:
            debug_pcm.extend(final_frame)
        yield final_frame

    # --- THE INDEPENDENT TASK ---
    if debug and debug_pcm:
        unique_path = os.path.join(debug_path, f"tts_{uuid.uuid4().hex}.wav")
        Path(debug_path).mkdir(parents=True, exist_ok=True)
        
        asyncio.create_task(
            asyncio.to_thread(save_debug_wav, unique_path, debug_pcm, SAMPLE_RATE)
        )


# ─── TTS Call — streaming ─────────────────────────────────────────────────────

async def call_tts_stream(
    http_client: httpx.AsyncClient,
    text: str,
    chunk_size: int = 4096,        # bytes per chunk (~2ms of audio at 48kHz)
    debug: bool = False,                                        # ← new
    debug_path: str = "./static/tts_debug"  # ← new
) -> AsyncGenerator[bytes, None]:
    
    """
    Streams raw PCM audio in 20ms frames. 
    Explicitly handles the difference between WAV (with header) and PCM (raw).
    This format is specifically optimized for WebRTC and low-latency VoIP applications. 
    Each 1,920-byte chunk you receive is ready to be pushed directly into an audio track buffer 
    without further processing.
    """
    buffer = b""
    header_stripped = False
    debug_pcm = bytearray() if debug else None

    # Ensure debug directory exists
    if debug:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
        print(f"\n── Test 4 Debug Mode Enabled ──")

    # API Request
    # Note: Use response_format="pcm" for raw data to avoid manual header stripping
    async with http_client.stream(
        "POST",
        f"{TTS_BASE_URL}/v1/audio/speech",
        json={
            "model": TTS_MODEL,
            "input": text,
            "voice": TTS_VOICE,
            "response_format": "pcm",  # Requested raw PCM
            "sample_rate": SAMPLE_RATE,
        },
        timeout=httpx.Timeout(timeout=None, connect=5.0)
    ) as resp:
        resp.raise_for_status()

        async for chunk in resp.aiter_bytes():
            if not chunk: # Explicit EOF check
                break
            buffer += chunk

            # Yield chunks as soon as we have enough for a 20ms frame
            while len(buffer) >= BYTES_PER_FRAME:
                frame = buffer[:BYTES_PER_FRAME]
                buffer = buffer[BYTES_PER_FRAME:]
                
                if debug:
                    debug_pcm.extend(frame)
                yield frame

        # Final Flush: If data is left over, pad it with silence to complete the frame
        if len(buffer) > 0:
            padding = BYTES_PER_FRAME - len(buffer)
            final_frame = buffer + (b"\x00" * padding)
            if debug:
                debug_pcm.extend(final_frame)
            yield final_frame


    # --- THE INDEPENDENT TASK ---
    if debug and debug_pcm:
        # Generate a unique filename so concurrent streams don't overwrite each other
        unique_path = os.path.join(debug_path, f"tts_{uuid.uuid4().hex}.wav")
        Path(debug_path).mkdir(parents=True, exist_ok=True)
        
        # Fire and forget: Move the bytes to a task. 
        # We pass a copy (bytes()) of the accumulator to ensure thread safety.
        asyncio.create_task(
            asyncio.to_thread(save_debug_wav, unique_path, debug_pcm, SAMPLE_RATE)
        )



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