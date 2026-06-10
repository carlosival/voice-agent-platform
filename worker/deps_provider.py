from yaafpy import ExecContext
from peer.types import PeerDependencies
from pipelines import audio_pipeline
from workflows.utils.tools import EndConversationTool
from workflows.utils.memory import InMemoryMemory
from workflows.utils.observavility import get_tracer
from httpx import AsyncClient
from workflows import AudioOutputTrack
import time
import os


end_conversation_tool = EndConversationTool()
http_client = AsyncClient(timeout=60.0)
tracer = get_tracer(http_client)

class DepProvider:
    def __init__(self):
        pass

    def build(self, session_id: str) -> PeerDependencies:

        # Generate a deterministic ID based on a seed
        session_trace_id = tracer.create_trace_id(seed=session_id)

        ctx = ExecContext(shared_data={
            "tools": {f"{end_conversation_tool.name}": end_conversation_tool},
            "session_id": session_id,
            "trace_context": {"trace_id": session_trace_id, "parent_span_id": ""},
            "peer_state": {
                "connected_at": time.time(),
                "last_activity": time.time(),
            },
            "metadata": {
                "token_usage": 0,
            },
            "message_history": InMemoryMemory(),
            "resources": {
                "output_track": AudioOutputTrack(),
                "http_client": http_client,
                "tracer": tracer,
                "pc": None, # Will be set when the peer is created
            }
        })

        def on_connected_fully(pc: RTCPeerConnection) -> None:
            ctx.shared_data["resources"]["pc"]

        def on_terminated(pc: RTCPeerConnection) -> None:
            ctx.shared_data["resources"]["pc"].close()

        return PeerDependencies(
            ctx=ctx,
            audio_handler=audio_pipeline,
            on_connected_fully=on_connected_fully,
            on_terminated=on_terminated
        )