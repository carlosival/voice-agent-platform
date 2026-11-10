import io, json
import wave
import numpy as np
from scipy import signal
from av import AudioFrame
import logging
from typing import Optional
import json
from copy import deepcopy

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000

# ─── Helpers ──────────────────────────────────────────────────────────────────

def frames_to_pcm(frames: list[np.ndarray]) -> bytes:

    return frames_to_mono_int16(frames).tobytes()

# ─── Primitive 1 — single frame to numpy ──────────────────────────────────────

def frame_to_mono_int32(frame: AudioFrame) -> np.ndarray:
    
    arr = frame.to_ndarray()

    if frame.layout.name == "stereo":
        if arr.ndim > 1 and arr.shape[0] == 2:
            # Planar: [[L,L,L...], [R,R,R...]]
            return (arr[0].astype(np.int32) + arr[1].astype(np.int32)) // 2
        else:
            # Interleaved: [L, R, L, R, ...]
            flat = arr.flatten()
            return (flat[0::2].astype(np.int32) + flat[1::2].astype(np.int32)) // 2

    return arr.flatten().astype(np.int32)


# ─── Primitive 2 — list of frames to numpy ────────────────────────────────────

def frames_to_mono_int16(frames: list[AudioFrame]) -> np.ndarray:
    """
    Concatenate aiortc AudioFrames into a flat mono int16 numpy array.
    No resampling — preserves original sample rate (48kHz from WebRTC).
    Use this when the consumer handles its own resampling (e.g. Speaches STT).
    """
    if not frames:
        return np.array([], dtype=np.int16)
    arrays = [frame_to_mono_int32(f) for f in frames]
    return np.concatenate(arrays).astype(np.int16)


# ─── Primitive 3 — resample numpy array ───────────────────────────────────────

def resample(pcm: np.ndarray, from_hz: int, to_hz: int) -> np.ndarray:
    """
    Resample a mono int16 numpy array from one sample rate to another.
    Uses polyphase resampling — applies anti-alias low-pass filter automatically.
    Returns int16 clipped to [-32768, 32767].
    
    Common usage:
        resample(pcm, 48000, 16000)  # WebRTC → Silero / TEN VAD
        resample(pcm, 16000, 48000)  # upsample back if needed
    """
    if from_hz == to_hz:
        return pcm

    from math import gcd
    g    = gcd(from_hz, to_hz)
    up   = to_hz   // g
    down = from_hz // g

    resampled = signal.resample_poly(pcm.astype(np.float32), up, down)
    return np.clip(resampled, -32768, 32767).astype(np.int16)


# ─── Primitive 4 — numpy to WAV bytes ─────────────────────────────────────────

def pcm_to_wav(pcm: np.ndarray, sample_rate: int) -> bytes:
    """
    Wrap a mono int16 numpy array in a WAV container.
    Returns raw WAV bytes — no file I/O.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


# ─── Primitive 5 — save WAV to disk ───────────────────────────────────────────

def save_wav(pcm: np.ndarray, sample_rate: int, path: str) -> None:
    """
    Save a mono int16 numpy array as a WAV file.
    Creates parent directories if needed.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wav_bytes = pcm_to_wav(pcm, sample_rate)
    with open(path, "wb") as f:
        f.write(wav_bytes)
    logger.info(f"WAV saved: {path} ({len(wav_bytes)} bytes, {sample_rate}Hz)")


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE, save_path: bool = True) -> bytes:
    """Wrap raw PCM int16 bytes in a WAV container — Whisper needs a file format."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)           # mono
        wf.setsampwidth(2)           # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    # Decouple file saving from the main function
    if save_path:
        with open("./static/stt_debug/" + str(uuid.uuid4()) + ".wav", "wb") as f:
            f.write(buf.getvalue())
    return buf.getvalue()


def save_debug_wav(path: str, data: bytes, sample_rate: int):
    """Synchronous file writing logic to be run in a thread or background."""
    try:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(data)
        logger.info(f"TTS Debug saved independently: {path}")
    except Exception as e:
        logger.error(f"Failed to save TTS debug file: {e}")


# ════════════════════════════════════════════════════════════
# VALID USER INPUT
# ════════════════════════════════════════════════════════════

def _is_valid_user_input(text: str) -> bool:
    text = text.strip()

    if len(text) < 3:
        return False

    # evita fragmentos típicos de ASR/interrupciones
    bad_endings = ("...", "eh", "mmm", "es tan", "y", "o")
    if any(text.endswith(e) for e in bad_endings):
        return False

    return True

# ════════════════════════════════════════════════════════════
# NOISE DETECTION
# ════════════════════════════════════════════════════════════

def _looks_like_noise(text: str) -> bool:

    words = text.lower().split()

    # "no no no no no"
    if len(words) > 5 and len(set(words)) <= 2:
        return True

    return False


def merge_dict(state: dict, update: dict) -> dict:
    """Merge an LLM-proposed update into the state, ignoring unknown keys
    and never overwriting a known value with null."""
    for key, value in update.items():
        if key not in state:
            continue  # unknown field: ignore
        if isinstance(state[key], dict) and isinstance(value, dict):
            merge_dict(state[key], value)
        elif value is not None:
            state[key] = value
    return state

def json_parser(json_str: str) -> Optional[dict[str, any]]:
            """
            Try to parse a JSON string to Dict.
            If it fails, attempt to extract the first {...} block.
            If that also fails, try to parse it as a Python literal.
            """
            try:
                # First try: normal JSON parsing
                json_dict = json.loads(json_str)
                if isinstance(json_dict, dict):
                    return json_dict
                else:
                    raise ValueError("Top-level JSON is not an object")
            
            except json.JSONDecodeError:
                # Second try: extract first {...} using regex
                match = re.search(r'\{.*\}', json_str, re.DOTALL)
                if match:
                    try:
                        json_dict = json.loads(match.group(0))
                        if isinstance(json_dict, dict):
                            return json_dict
                    except json.JSONDecodeError:
                        pass  # Continue to literal_eval fallback

                # Third try: attempt to parse as Python literal
                try:
                    python_dict = ast.literal_eval(json_str)
                    if isinstance(python_dict, dict):
                        # Convert to JSON-compatible: ensure keys are strings etc.
                        json_string = json.dumps(python_dict)
                        json_dict = json.loads(json_string)
                        return json_dict
                except (ValueError, SyntaxError):
                    pass

            # If everything fails, return a fallback
            logger.warning(f"JSON parsing failed for: {json_str[:100]}...")
            
            raise ValueError("Error parsing str as json")