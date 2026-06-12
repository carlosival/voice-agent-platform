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

from dbs_clients import AsyncSessionFactory, VoiceAgent
from fastapi.utils.speech_to_text import call_stt_from_frames_openai, call_stt_from_frames_speaches
from fastapi.utils.text_to_speech import call_tts_stream_google, call_tts_stream_speaches
from fastapi.utils.call_llm import call_llm_stream_openai

logger = logging.getLogger(__name__)

# Global singletons
end_conversation_tool = EndConversationTool()
http_client = AsyncClient(timeout=60.0)
tracer = get_tracer(http_client)

class DepProvider:
    @staticmethod
    async def build(session_id: str, agent_id: str) -> PeerDependencies:
        """
        Dynamically builds the execution context and dependencies for a voice session
        based on the agent configuration stored in the database.
        """
        
        # 1. Fetch Agent Config from DB
        async with AsyncSessionFactory() as session:
            stmt = select(VoiceAgent).where(VoiceAgent.id == agent_id)
            result = await session.execute(stmt)
            agent = result.scalar_one_or_none()
            
            if not agent:
                logger.error(f"Agent {agent_id} not found in database. Using defaults.")
                # Fallback or raise error? For now, let's assume we need it.
                raise RuntimeError(f"Agent {agent_id} not found.")

        # 2. Configure STT Function
        stt_config = agent.stt_config
        if stt_config.get("engine") == "speaches":
            stt_func = call_stt_from_frames_speaches
        else:
            stt_func = call_stt_from_frames_openai

        # 3. Configure TTS Function
        tts_config = agent.tts_config
        if tts_config.get("engine") == "speaches":
            tts_func = call_tts_stream_speaches
        elif tts_config.get("engine") == "google":
            tts_func = call_tts_stream_google
        else:
            tts_func = call_tts_stream_speaches # Fallback

        # 4. Configure LLM Function
        llm_func = call_llm_stream_openai # Currently only OpenAI-compatible supported

        # 5. Build Trace Context
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