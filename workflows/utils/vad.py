import os
import io
import wave
import numpy as np
import httpx
import torch
from httpx import AsyncClient
from scipy.signal import resample_poly


SAMPLE_RATE        = 48000
SILERO_SAMPLE_RATE = 16000                          # Silero expects 16kHz
VAD_ENDPOINT       = f"{os.getenv('VAD_BASE_URL', 'http://speaches:8000')}/v1/audio/speech/timestamps"
RMS_THRESHOLD      = float(os.getenv("RMS_THRESHOLD", "500"))

# GPU if available, CPU fallback
_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw PCM int16 bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def frames_to_pcm(frames: list) -> bytes:
    """Concatenate aiortc AudioFrames into raw PCM bytes."""
    arrays = [np.array(f.to_ndarray(), dtype=np.int16).flatten() for f in frames]
    return np.concatenate(arrays).tobytes()


def frames_to_numpy(frames: list) -> np.ndarray:
    """Concatenate aiortc AudioFrames into a single int16 numpy array."""
    arrays = [np.array(f.to_ndarray(), dtype=np.int16).flatten() for f in frames]
    return np.concatenate(arrays)


def _downsample_to_silero(pcm: np.ndarray) -> np.ndarray:
    """Downsample from 48kHz to 16kHz by taking every 3rd sample."""
    #return pcm[::3]
    # up=1, down=3 → 48000 * (1/3) = 16000
    return resample_poly(pcm, up=1, down=3).astype(np.float32)


# ════════════════════════════════════════════════════════════════════════════════
# LAYER 1 — RMS energy  (~0ms, no model, no network)
# ════════════════════════════════════════════════════════════════════════════════

def rms_has_speech(
    frames:    list,
    threshold: float = RMS_THRESHOLD,
) -> bool:
    """
    Instant energy-based pre-filter. Returns True if RMS amplitude exceeds
    threshold. Runs every frame at negligible cost — gates Silero so it is
    never called on silence.

    Args:
        frames:     list of aiortc AudioFrames
        threshold:  RMS amplitude (0–32767). Env: RMS_THRESHOLD (default 500).
                    Lower → more sensitive, higher → ignores quiet speech.
    """
    pcm = frames_to_numpy(frames).astype(np.float32)
    rms = float(np.sqrt(np.mean(pcm ** 2)))
    return rms > threshold


# ════════════════════════════════════════════════════════════════════════════════
# LAYER 2 — Silero VAD local on GPU  (~0.1–0.3ms, no network)
# ════════════════════════════════════════════════════════════════════════════════

_silero_model = None   # lazy-loaded singleton


def _get_silero_model():
    """Load Silero VAD once, move to GPU, reuse for all calls."""
    global _silero_model
    if _silero_model is None:
        from silero_vad import load_silero_vad
        _silero_model = load_silero_vad().to(_device)
    return _silero_model


def silero_has_speech(
    frames:    list,
    threshold: float = 0.7,
) -> bool:
    """
    Local Silero VAD — processes audio in 512-sample chunks (required by model).
    Returns True if any chunk exceeds the confidence threshold.
    """
    model  = _get_silero_model()
    pcm    = frames_to_numpy(frames)
    pcm_16 = _downsample_to_silero(pcm)   # 48kHz → 16kHz

    # Silero requires exactly 512 samples per chunk at 16kHz
    chunk_size = 512
    for i in range(0, len(pcm_16), chunk_size):
        chunk = pcm_16[i:i + chunk_size]

        # Skip incomplete last chunk
        if len(chunk) < chunk_size:
            break

        audio_tensor = torch.from_numpy(
            chunk.astype(np.float32) / 32768.0
        ).to(_device)

        confidence = model(audio_tensor, SILERO_SAMPLE_RATE).item()
        if confidence >= threshold:
            return True

    return False


def silero_has_speech_from_numpy(
    pcm:       np.ndarray,
    threshold: float = 0.7,
) -> bool:
    model  = _get_silero_model()
    pcm_16 = _downsample_to_silero(pcm)

    chunk_size = 512
    for i in range(0, len(pcm_16), chunk_size):
        chunk = pcm_16[i:i + chunk_size]
        if len(chunk) < chunk_size:
            break
        audio_tensor = torch.from_numpy(
            chunk.astype(np.float32) / 32768.0
        ).to(_device)
        confidence = model(audio_tensor, SILERO_SAMPLE_RATE).item()
        if confidence >= threshold:
            return True

    return False


# ════════════════════════════════════════════════════════════════════════════════
# LAYERED — two-layer pipeline for vad_gate
# ════════════════════════════════════════════════════════════════════════════════

def layered_has_speech(
    frames:           list,
    rms_threshold:    float = RMS_THRESHOLD,
    silero_threshold: float = 0.5,
) -> bool:
    """
    Two-layer VAD — cheapest check first:

        Layer 1: RMS energy    ~0ms     — skips silence instantly
        Layer 2: Silero GPU    ~0.2ms   — accurate speech detection

    Speaches VAD is intentionally excluded — it runs the same Silero model
    internally and adds TCP + HTTP overhead with no accuracy benefit.
    Use call_vad() only when you need segment timestamps.

    Synchronous — no await needed in vad_gate.

    Usage in vad_gate:
        speech_detected = layered_has_speech(list(vad_window))
    """
    # Layer 1 — RMS (~0ms)
    if not rms_has_speech(frames, threshold=rms_threshold):
        return False

    # Layer 2 — Silero GPU (~0.2ms)
    return silero_has_speech(frames, threshold=silero_threshold)


# ════════════════════════════════════════════════════════════════════════════════
# Speaches VAD — only for segment timestamps
# ════════════════════════════════════════════════════════════════════════════════

async def call_vad(
    http_client:           AsyncClient,
    audio:                 bytes,
    is_wav:                bool        = False,
    threshold:             float       = 0.5,
    max_speech_duration_s: float | None = None,
) -> list[dict]:
    """
    Speaches VAD endpoint — returns speech segments with timestamps.
    Use this only when you need [{"start": int, "end": int}] sample indices.
    For True/False detection use layered_has_speech() instead.

    Args:
        audio:                  raw PCM bytes OR WAV bytes
        is_wav:                 True if already WAV
        threshold:              confidence threshold (0–1)
        max_speech_duration_s:  split long segments (optional)
    """
    wav_bytes = audio if is_wav else pcm_to_wav(audio)

    data: dict = {"threshold": str(threshold)}
    if max_speech_duration_s is not None:
        data["max_speech_duration_s"] = str(max_speech_duration_s)

    resp = await http_client.post(
        VAD_ENDPOINT,
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data=data,
    )
    resp.raise_for_status()
    return resp.json()    # e.g. [{"start": 64, "end": 1323}]


# ════════════════════════════════════════════════════════════════════════════════
# Test helpers
# ════════════════════════════════════════════════════════════════════════════════
 
WAV_TEST_FILE = "static/test_transcribe.wav"
 
 
def _load_speech_pcm() -> np.ndarray:
    """Load WAV_TEST_FILE and resample to 48kHz if needed."""
    with wave.open(WAV_TEST_FILE, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
        sr  = wf.getframerate()
    pcm = np.frombuffer(raw, dtype=np.int16)
    if sr != SAMPLE_RATE:
        import scipy.signal as sig
        pcm = sig.resample(pcm, int(len(pcm) * SAMPLE_RATE / sr)).astype(np.int16)
    return pcm
 
 
def _make_silence_pcm(duration_s: float = 2.0) -> np.ndarray:
    """Pure silence — all zeros."""
    return np.zeros(int(SAMPLE_RATE * duration_s), dtype=np.int16)
 
 
def _make_noise_pcm(duration_s: float = 2.0, amplitude: float = 200) -> np.ndarray:
    """Low-level background noise with RMS below default threshold (500)."""
    return (np.random.randn(int(SAMPLE_RATE * duration_s)) * amplitude).astype(np.int16)
 
 
class FakeFrame:
    """Minimal aiortc AudioFrame stub for testing."""
    def __init__(self, data: np.ndarray):
        self._data = data
    def to_ndarray(self) -> np.ndarray:
        return self._data.reshape(1, -1)
 
 
def _result(actual: bool, expected: bool) -> str:
    status = "✅ PASS" if actual == expected else "❌ FAIL"
    return f"{status}  got={actual}  expected={expected}"
 
 
# ════════════════════════════════════════════════════════════════════════════════
# Test
# ════════════════════════════════════════════════════════════════════════════════
 
async def _test():
    print(f"Device:       {_device}")
    print(f"VAD endpoint: {VAD_ENDPOINT}\n")
 
    if not os.path.exists(WAV_TEST_FILE):
        print(f"❌ {WAV_TEST_FILE} not found — place it in the working directory")
        return
 
    print(f"  Source: {WAV_TEST_FILE} (real speech)\n")
 
    speech_pcm  = _load_speech_pcm()
    silence_pcm = _make_silence_pcm()
    noise_pcm   = _make_noise_pcm()
 
    speech_frames  = [FakeFrame(speech_pcm)]
    silence_frames = [FakeFrame(silence_pcm)]
    noise_frames   = [FakeFrame(noise_pcm)]
 
    # ── Layer 1: RMS ──────────────────────────────────────────────────────────
    print("── Test 1: rms_has_speech ──")
    print(f"  Real speech : {_result(rms_has_speech(speech_frames),  expected=True)}")
    print(f"  Silence     : {_result(rms_has_speech(silence_frames), expected=False)}")
    print(f"  Background  : {_result(rms_has_speech(noise_frames),   expected=False)}\n")
 
    # ── Layer 2: Silero ───────────────────────────────────────────────────────
    print("── Test 2: silero_has_speech ──")
    print(f"  Real speech : {_result(silero_has_speech(speech_frames),  expected=True)}")
    print(f"  Silence     : {_result(silero_has_speech(silence_frames), expected=False)}")
    print(f"  Background  : {_result(silero_has_speech(noise_frames),   expected=False)}\n")
 
    # ── Layered ───────────────────────────────────────────────────────────────
    print("── Test 3: layered_has_speech ──")
    print(f"  Real speech : {_result(layered_has_speech(speech_frames),  expected=True)}")
    print(f"  Silence     : {_result(layered_has_speech(silence_frames), expected=False)}")
    print(f"  Background  : {_result(layered_has_speech(noise_frames),   expected=False)}\n")
 
    # ── Speaches call_vad — timestamps on real speech vs silence ──────────────
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("── Test 4: call_vad timestamps ──")
        print(f"  Source: {WAV_TEST_FILE} vs generated silence\n")
 
        wav_speech  = open(WAV_TEST_FILE, "rb").read()
        wav_silence = pcm_to_wav(silence_pcm.tobytes())
 
        segments_speech  = await call_vad(client, wav_speech,  is_wav=True)
        segments_silence = await call_vad(client, wav_silence, is_wav=True)
 
        speech_detected  = len(segments_speech)  > 0
        silence_detected = len(segments_silence) > 0
 
        print(f"  Speech  segments : {segments_speech}")
        print(f"  Silence segments : {segments_silence}")
        print(f"  Speech  detected : {_result(speech_detected,  expected=True)}")
        print(f"  Silence detected : {_result(silence_detected, expected=False)}\n")
 
 
if __name__ == "__main__":
    import asyncio
    asyncio.run(_test())