import asyncio
import httpx
from yaafpy import StreamWorkflow, ExecContext, Workflow
import logging

logger = logging.getLogger(__name__)

from workflows.steps import (
    vad_gate,
    brain_bridge,
    stt_stream,
    llm_stream,
    tts_stream
)


def build_basic_agentic_voice_workflow() -> Workflow:
    """Build and return the reusable workflow definition."""
    wf = Workflow()
    (
        wf
        .use(sanitize,           name="sanitize",    description="Sanitize the input")
        .use(llm_stream,    name="llm",    description="Llama 3.1 streaming")
        .use(action,           name="action",    description="Action")
    )
    return wf

def build_voice_workflow() -> StreamWorkflow:
    """Build and return the reusable workflow definition."""
    wf = StreamWorkflow()
    (
        wf
        .use(vad_gate,      name="vad",    description="VAD utterance gating")
        #.use(brain_bridge,  name="bridge") # THE DECOUPLER (Stage 1.5)
        .use(stt_stream,    name="stt",    description="Speaches Whisper STT")
        .use(llm_stream,    name="llm",    description="Llama 3.1 streaming")
        .use(tts_stream,    name="tts",    description="Speaches Kokoro TTS")
    )
    return wf

VOICE_WORKFLOW = build_voice_workflow()     # singleton — reuse across sessions
