from yaafpy import ExecContext
from peer.types import PeerDependencies
from pipelines.audio_pipeline import audio_pipeline
from workflows.utils.tools import EndConversationTool
from workflows.utils.memory import InMemoryMemory
from workflows.utils.observavility import get_tracer
from httpx import AsyncClient
from workflows.steps.outputs import AudioOutputTrack
from httpx import AsyncClient
from aiortc import RTCPeerConnection, RTCIceCandidate
from aiortc.sdp import candidate_to_sdp
from peer.types import PeerSession
import logging
import json
from dbs_clients import redis_client
from typing import Any
import time
import asyncio
from workflows.utils.context import get_prompt
from services.vault.secrets import Secrets


logger = logging.getLogger(__name__)

# Global singletons
end_conversation_tool = EndConversationTool()
http_client = AsyncClient(timeout=60.0)
tracer = get_tracer(http_client)
vault = Secrets(lambda key, path: None)
tools = [end_conversation_tool]



class DepProvider:
    @staticmethod
    async def build(session_id: str, agent_config: dict[str, Any] = None, active_sessions: dict[str, PeerSession] = None) -> PeerDependencies:
        """
        Dynamically builds the execution context and dependencies for a voice session
        based on the agent configuration stored in the cache and database.
        """
        
        # --- Redis stream keys ---
        # Gateway → Worker  (offer + client ICE)
        # Worker  → Gateway (answer + worker ICE)  ─ keyed by message type inside payload
        answer_stream_key    = f"webrtc:answer:{session_id}" # worker -> client
        ice_stream_key       = f"webrtc:client:ice:{session_id}" # client forward -> worker listen
        ice_stream_key_worker = f"webrtc:worker:ice:{session_id}" # worker forward -> client listen

        logger.info(f"Agent config: {agent_config}")

        # 1. Load Trace Context
        session_trace_id = tracer.create_trace_id(seed=session_id)

        # 2. Load Tools
        tools_registry = {}
        tool_configs = agent_config.get("llm_config", {}).get("tools",[])
        
        for tool in tools:
            if tool.name in tool_configs:
                tools_registry[tool.name] = tool

        # 3. Load Prompts
        system_prompt = get_prompt(agent_config.get("llm_config", {}).get("system_prompt", None))

        ctx = ExecContext(shared_data={
            "tools": tools_registry,
            "system_prompt": system_prompt,
            "llm_api_key": vault.get_secret(agent_config.get("public_key"), "LLM_API_KEY"),
            "stt_api_key": vault.get_secret(agent_config.get("public_key"), "STT_API_KEY"),
            "tts_api_key": vault.get_secret(agent_config.get("public_key"), "TTS_API_KEY"),
            "llm_temperature": agent_config.get("llm_config", {}).get("temperature", None),
            "llm_max_tokens": agent_config.get("llm_config", {}).get("max_tokens", None),
            "llm_top_p": agent_config.get("llm_config", {}).get("top_p", None),
            "tts_provider":agent_config.get("tts_config",{}).get("engine", None),
            "stt_provider": agent_config.get("stt_config",{}).get("engine",None),
            "session_id": session_id,
            "trace_context": {"trace_id": session_trace_id, "parent_span_id": ""},
            "peer_state": {
                "connected_at": time.time(),
                "last_activity": time.time(),
            },
            "stt_config": agent_config.get("stt_config",{}),
            "tts_config": agent_config.get("tts_config",{}),
            "llm_config": agent_config.get("llm_config",{}),
            "message_history": InMemoryMemory(),
            "resources": {
                "output_track": AudioOutputTrack(),
                "http_client": http_client,
                "tracer": tracer,
                "pc": None, # Will be set when the peer is created
            }
        })

        
        #ALl thing related to webrtc connection should go to webrtc connection approvisioning
        # With a WebrtcpeerDepency object.

        def on_connected_fully() -> None:
            pass

        def on_ice_candidate(candidate: RTCIceCandidate) -> None:
            if not candidate:
                # End of candidates sentinel
                payload = json.dumps({"candidate": "", "sdpMid": "", "sdpMLineIndex": 0})
            else:
                sdp_line = candidate_to_sdp(candidate)
                # Ensure "candidate:" prefix is present for client compatibility
                if not sdp_line.startswith("candidate:"):
                    sdp_line = f"candidate:{sdp_line}"
                    
                payload = json.dumps({
                    "candidate": sdp_line,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex
                })
            
            logger.info(f"Sending ICE candidate to client: {payload}")
            # Use background task since this is a synchronous callback from aiortc
            asyncio.create_task(redis_client.xadd(ice_stream_key_worker, {"payload": payload}))
            

        def on_terminated() -> None:
            logger.info("Session terminated — cleaning up")
            # ctx is already in scope via closure
            asyncio.create_task(ctx.shared_data["resources"]["pc"].close())
            active_sessions.pop(session_id, None)  


        return PeerDependencies(
            ctx=ctx,
            audio_handler=audio_pipeline,
            on_connected_fully=on_connected_fully,
            on_ice_candidate=on_ice_candidate,
            on_terminated=on_terminated,
        )