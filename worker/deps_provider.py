from yaafpy import ExecContext
from peer.types import PeerDependencies
from pipelines import audio_pipeline
from workflows.utils.tools import EndConversationTool
from workflows.utils.memory import InMemoryMemory
from workflows.utils.observavility import get_tracer
from httpx import AsyncClient
from workflows import AudioOutputTrack
from httpx import AsyncClient
from aiortc import RTCPeerConnection
import logging
from dbs_clients import redis_client
from typing import Any

logger = logging.getLogger(__name__)

# Global singletons
end_conversation_tool = EndConversationTool()
http_client = AsyncClient(timeout=60.0)
tracer = get_tracer(http_client)

class DepProvider:
    @staticmethod
    async def build(session_id: str, agent_config: dict[str, Any] = None) -> PeerDependencies:
        """
        Dynamically builds the execution context and dependencies for a voice session
        based on the agent configuration stored in the cache and database.
        """
        
        # 1. Build Trace Context
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

        def on_connected_fully() -> None:
            asyncio.create_task(
                redis_client.xadd(
                    answer_stream_key,
                    {"event": "connected"},
                    maxlen=10
                )
            )
            
            

        def on_terminated() -> None:
            logger.info("Session terminated — cleaning up")
            # ctx is already in scope via closure
            ctx.shared_data["resources"]["pc"].close()
            ctx.shared_data["resources"].pop("pc", None)    

        return PeerDependencies(
            ctx=ctx,
            audio_handler=audio_pipeline,
            on_connected_fully=on_connected_fully,
            on_terminated=on_terminated
        )