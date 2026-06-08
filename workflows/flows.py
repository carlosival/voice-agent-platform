import asyncio
import httpx
from yaafpy import StreamWorkflow, ExecContext
import logging

logger = logging.getLogger(__name__)

from steps import (
    vad_gate,
    brain_bridge,
    stt,
    llm_stream,
    tts,
    frame_sender,
)

def build_voice_workflow() -> StreamWorkflow:
    """Build and return the reusable workflow definition."""
    wf = StreamWorkflow()
    (
        wf
        .use(vad_gate,      name="vad",    description="VAD utterance gating")
        .use(brain_bridge,  name="bridge") # THE DECOUPLER (Stage 1.5)
        .use(stt,           name="stt",    description="Speaches Whisper STT")
        .use(llm_stream,    name="llm",    description="Llama 3.1 streaming")
        .use(tts,           name="tts",    description="Speaches Kokoro TTS")
    )
    return wf

VOICE_WORKFLOW = build_voice_workflow()     # singleton — reuse across sessions
