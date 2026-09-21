import os, uuid, wave
import numpy as np
from pathlib import Path

from workflows.utils import frames_to_mono_int16
from av import AudioFrame

import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Directory containing debug_stt.py
WORKER_DIR = Path(__file__).resolve().parent

# workflows/steps/stt/workers/stt_debug/
DEBUG_DIR = WORKER_DIR / "stt_debug"



async def debug_stt(frames: list[AudioFrame]) -> str:
                
                # Setup Debug Directory
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                debug_dir = DEBUG_DIR
                
                # ---------------------------------------------------------
                # AudioFrame -> float32 samples
                #
                # Expected:
                #   float32
                #   mono
                #   16000 Hz
                # ---------------------------------------------------------
                chunks = []

                for frame in frames:
                    samples = frame.to_ndarray()

                    # mono frame normally has shape (1, samples)
                    samples = samples.reshape(-1)

                    chunks.append(samples.astype(np.float32))

                audio = np.concatenate(chunks)

                # ---------------------------------------------------------
                # float32 [-1.0, 1.0] -> PCM S16LE
                # ---------------------------------------------------------
                audio = np.clip(audio, -1.0, 1.0)

                pcm = (audio * 32767.0).astype(np.int16)

    

                # 2. DEBUG LOGGING: Save the exact PCM sent to Whisper
                # We do this before the task so we have the file even if the task is cancelled
                filename = os.path.join(debug_dir, f"{uuid.uuid4().hex}_{len(frames)}frames.wav")
                try:
                    with wave.open(filename, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(16000)   # Faster-Whisper standard
                        wf.writeframes(pcm.tobytes())
                    logger.info(f"STT debug saved: {filename} | samples={len(pcm)} | rms={np.sqrt(np.mean(pcm.astype(np.float32)**2)):.1f}")
                except Exception as e:
                    logger.error(f"STT Debug Save Failed: {e}")