from yaafpy import ExecContext
from peer.types import PeerDependencies
from pipelines import audio_pipeline
from tools.tools import EndConversationTool
from memory.in_memory import InMemoryMemory
from httpx import AsyncClient
import time


end_conversation_tool = EndConversationTool()
memory = InMemoryMemory()
output_track = None
http_client = AsyncClient(timeout=60.0)
tracer = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST"),
        httpx_client=http_client
    )

class DepProvider:
    def __init__(self):
        pass

    def build(self, session_id: str) -> PeerDependencies:

        

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
            "message_history": memory,
            "resources": {
                "output_track": output_track,
                "http_client": http_client,
                "tracer": tracer,
            }
        })
        return PeerDependencies(
            ctx=ctx,
            audio_handler=audio_pipeline,
        )