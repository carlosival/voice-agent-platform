from yaafpy import ExecContext
from src.peer import PeerDependencies, PeerSession
from src.pipelines.audio_pipeline import audio_pipeline
from workflows.utils.tools import EndConversationTool
from workflows.utils.memory import InMemoryMemory
from workflows.utils.observavility import get_tracer
from httpx import AsyncClient
from workflows.steps.outputs import AudioOutputTrack
from httpx import AsyncClient
from aiortc import RTCPeerConnection, RTCIceCandidate
from aiortc.sdp import candidate_to_sdp

import logging, json, time, asyncio, re

from src.clients.redis_db import redis_client

from typing import Any

from src.services.vault.infiscal import get_secrets


logger = logging.getLogger(__name__)

# Global singletons

http_client = AsyncClient(timeout=60.0)
tracer = get_tracer(http_client)
TOOL_CLASSES = {'end_conversation': EndConversationTool}


_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_template(template: str, values: dict, *, strict: bool = False) -> str:
    """Replace {{name}} with values["name"].

    strict=False: unknown placeholders stay as {{name}} (useful for state
                  placeholders that are filled later during the call).
    strict=True:  unknown placeholders raise KeyError.
    """
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in values:
            return str(values[name])
        if strict:
            raise KeyError(f"Missing value for placeholder: {name}")
        return m.group(0)               # leave it untouched

    return _PLACEHOLDER.sub(repl, template)


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

        pk_id = agent_config["meta"]["pk_id"]

        # 1. Load Trace Context
        session_trace_id = tracer.create_trace_id(seed=session_id)

        # 2. Load Tools
        tools_registry = {}
        tool_configs = agent_config.get("tools",{})
        
        for key, spec in agent_config["tools"]:
            cls = TOOL_CLASSES.get(key)
            if cls is None:
                raise AgentConfigError(f"No implementation for tool: {key}")
            
            # Inject public_key_id in all configurations is essential for vault to form the path and find key
            # Maybe if not inyected from db configurations
            cfg = copy.deepcopy(spec["config"])
            cfg["pk_id"] = pk_id
            tools_registry[key] = cls(cfg)

        # 3. Load Prompts
        system_prompt = render_template(agent_config["prompt"]["template"], agent_config["input"])
        
        #system_prompt = get_prompt(agent_config.get("llm_config", {}).get("system_prompt", None))

        secrets = get_secrets(f"/{pk_id}",["LLM_API_KEY", "STT_API_KEY", "TTS_API_KEY"])


        ctx = ExecContext(shared_data={
            "tools": tools_registry,
            "system_prompt": system_prompt,
            "llm_api_key": secrets[0],
            "stt_api_key": secrets[1],
            "tts_api_key": secrets[2],
            "tts_provider":agent_config.get("models",{}).get("tts",{}).get("engine", None),
            "stt_provider": agent_config.get("models",{}).get("stt",{}).get("engine",None),
            "session_id": session_id,
            "trace_context": {"trace_id": session_trace_id, "parent_span_id": ""},
            "peer_state": {
                "connected_at": time.time(),
                "last_activity": time.time(),
            },
            "stt_config": agent_config.get("models",{}).get("stt",{}),
            "tts_config": agent_config.get("models",{}).get("tts",{}),
            "llm_config": agent_config.get("models", {}).get("llm",{}),
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