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

from opentelemetry import trace
from openinference.semconv.trace import (
    SpanAttributes,
    AudioAttributes,
    OpenInferenceSpanKindValues,
    OpenInferenceMimeTypeValues,
)


import logging, json, time, asyncio, re, copy

from src.clients.redis_db import redis_client

from typing import Any

from src.services.vault.infiscal import get_secrets

from workflows.utils.observavility.voice_otel import setup_tracing, get_tracer, get_provider 

logger = logging.getLogger(__name__)
trace_providder = setup_tracing()


# Global singletons

http_client = AsyncClient(timeout=60.0)
tracer = get_tracer()
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

        pk_id = str(agent_config.get("meta", {}).get("pk_id", ""))

        # 1. Start the root conversation span
        conversation_span = tracer.start_span(
            "voice_conversational_agent",
            attributes={
                f"{SpanAttributes.SESSION_ID}": session_id,
                "agent_id": str(agent_config.get("meta", {}).get("agent_id", "")),
                f"{SpanAttributes.PROMPT_ID}": str(agent_config.get("prompt", {}).get("id", "")),
                "state": str(agent_config.get("state", {}).get("id", "")), 
                "tools": ",".join([tool["id"] for tool in agent_config.get("tools",{}).values()]),
                f"{SpanAttributes.OPENINFERENCE_SPAN_KIND}": str(agent_config.get("meta", {}).get("strategy", "")),
                f"{SpanAttributes.AGENT_NAME}": str(agent_config.get("meta", {}).get("name", "")),
                f"{SpanAttributes.USER_ID}": str(agent_config.get("meta", {}).get("pk_id", "")),
                
                # Refactor Add other related attr that allows to track back state, prompt version, tools, etc
                # How this agent was build from config
            },
        )

        

        # 2. Load Tools
        tools_registry = {}
        tool_configs = agent_config.get("tools",{})
        
        for key, spec in tool_configs.items():
            cls = TOOL_CLASSES.get(key)
            if cls is None:
                raise AgentConfigError(f"No implementation for tool: {key}")
            
            # Inject public_key_id in all configurations is essential for vault to form the path and finds the keys
            cfg = copy.deepcopy(spec.get("config", {}))
            cfg["pk_id"] = pk_id
            tools_registry[key] = cls(cfg)

        # 3. Load Prompts
        system_prompt = render_template(agent_config["prompt"]["template"], agent_config["input"])
        
        #system_prompt = get_prompt(agent_config.get("llm_config", {}).get("system_prompt", None))

        secrets = get_secrets(f"/{pk_id}",["LLM_API_KEY", "STT_API_KEY", "TTS_API_KEY"])


        ctx = ExecContext(shared_data={
            "tools": tools_registry, # Add tool related DB ID
            "system_prompt": system_prompt, # Add Prompt Version Related ID
            "llm_api_key": secrets[0],
            "stt_api_key": secrets[1],
            "tts_api_key": secrets[2],
            "tts_provider":agent_config.get("models",{}).get("tts",{}).get("engine", None),
            "stt_provider": agent_config.get("models",{}).get("stt",{}).get("engine",None),
            "session_id": session_id,
            "trace_context": { 
                               "tracer":tracer,  
                               "parent_span": None,
                               "conversation_span": conversation_span,
                            },
            "stt_config": agent_config.get("models",{}).get("stt",{}),
            "tts_config": agent_config.get("models",{}).get("tts",{}),
            "llm_config": agent_config.get("models", {}).get("llm",{}),
            "message_history": InMemoryMemory(),
            "resources": {
                "output_track": AudioOutputTrack(),
                "http_client": http_client,
                "tracer": tracer,
                "tracer_provider": trace_providder,
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
            # conversation span will be close in the pipeline with the respective Error o Success msg
    
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