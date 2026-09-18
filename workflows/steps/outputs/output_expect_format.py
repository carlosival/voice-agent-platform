
import numpy as np
from enum import Enum
from dataclasses import dataclass
from .audio_track import AudioOutputTrack
from .audio_output import IAudioOutput

import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Assumed shape of your Encoding enum; align with your real definition ---
class Encoding(str, Enum):
    PCM_S16LE = "pcm_s16le"
    PCM_S32LE = "pcm_s32le"
    PCM_F32LE = "pcm_f32le"
    PCM_S24LE = "pcm_s24le"
    PCM_U8 = "pcm_u8"
    MULAW = "mulaw"
    ALAW = "alaw"
    OPUS = "opus"
    
class Container(str, Enum):
    RAW = "raw"
    WAV = "wav"
    OGG = "ogg"
    MP3 = "mp3"
    AAC = "aac"
    OPUS = "opus"
    FLAC = "flac"
    ALAW = "alaw"
    ULAW = "ulaw"

_DTYPE_MAP = {
    Encoding.PCM_S16LE: np.int16,
    Encoding.PCM_S32LE: np.int32,
    Encoding.PCM_F32LE: np.float32,
    Encoding.PCM_U8: np.uint8,
}


@dataclass(frozen=True)
class AudioFormat:
    encoding: Encoding
    sample_rate: int
    channels: int
    sample_width: int
    container: Container = "raw"
    


@dataclass
class AudioChunk:
    data: bytes
    format: AudioFormat
    timestamp_ns: int
    sequence: int
    is_final: bool = False

WEBRTC_OUTPUT_FORMAT = AudioFormat(
    encoding=Encoding.PCM_S16LE,
    sample_rate=48000,
    channels=1,
    container="raw",
    sample_width=2,
)


SPEACHES_PIPER_TTS_FORMAT = AudioFormat(
    encoding=Encoding.PCM_S16LE,
    sample_rate=22050,  # depends on the Piper voice/model
    channels=1,
    container="raw",
    sample_width=2,
)

SPEACHES_KOKORO_TTS_FORMAT = AudioFormat(
    encoding=Encoding.PCM_S16LE,
    sample_rate=24000,
    channels=1,
    container="raw",
    sample_width=2,
)

OPENAI_TTS_FORMAT = AudioFormat(
    encoding=Encoding.PCM_S16LE,
    sample_rate=24000,
    channels=1,
    container="raw",
    sample_width=2,
)

GROQ_TTS_FORMAT = AudioFormat(
    encoding=Encoding.PCM_S16LE,
    sample_rate=24000,
    channels=1,
    container="raw",
    sample_width=2,
)


TELNYX_OUTPUT_FORMAT = AudioFormat(
    encoding=Encoding.PCM_S16LE,
    sample_rate=8000,
    channels=1,
    container="raw",
    sample_width=2,
)



def get_tts_provider_format(provider_name: str, model:str) -> AudioFormat:
    
    if provider_name == "openai":
        return OPENAI_TTS_FORMAT
    elif provider_name == "groq":
        return GROQ_TTS_FORMAT
    elif provider_name == "telnyx":
        return SPEACHES_TTS_FORMAT
    elif provider_name == "speaches":
        model_lower = model.lower()
        if "piper" in model_lower:
            return SPEACHES_PIPER_TTS_FORMAT 
        if "kokoro" in model_lower:
            return  SPEACHES_KOKORO_TTS_FORMAT      
    else:
        raise ValueError(f"Unknown provider: {provider_name}")

def get_output_format(output: IAudioOutput) -> AudioFormat:
    """
    Returns the expected audio format for the given output.
    """
    logger.info("Object type: %s", type(output).__name__)
    logger.info("Object is not None: %s", output is not None)
    logger.info("AudioOutputTrack (expected) module: %s, id: %s", AudioOutputTrack.__module__, id(AudioOutputTrack))
 
    if isinstance(output, AudioOutputTrack):
        logger.info("return correct output formart")
        return WEBRTC_OUTPUT_FORMAT
 
    # BUG FIX: previously there was no else/raise here, so an unhandled
    # output type silently fell off the end of the function and returned
    # None. That None would then propagate into AudioConverter(...) (or
    # wherever the format is consumed) with a confusing error far from the
    # real cause. Fail loudly and specifically instead.
    raise ValueError(
        f"No known audio format for output type: {type(output).__name__}"
    )
        