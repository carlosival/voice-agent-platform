"""
voice_otel.py - OpenTelemetry + OpenInference tracing for the voice pipeline.

Follows Arize's manual-instrumentation docs: register() -> OITracer -> spans with
openinference.span.kind / input.value / output.value, explicit OK/ERROR status.

Replaces tracing.py, voice_tracing.py and pipeline_tracing.py.

    conversation                      (AGENT, session.id)
    ├── turn_N                        (CHAIN)  opened on StartSpeaking, closed when the agent finishes
    │   ├── vad                       (CHAIN)  real start..end of the user's speech
    │   ├── stt_utterance             (CHAIN)
    │   ├── llm_stream_generation     (LLM)    via ConversationTrace.begin() in call_llm_stream_openai
    │   ├── tool:*                    (TOOL)
    │   └── tts_sentence_N            (CHAIN)

Why the parent context is passed explicitly: OTel's "current span" lives in a contextvar,
and your stages run as separate generators/tasks, so the turn span is NOT current inside
them. ConversationTrace keeps the turn's Context and hands it to every span it creates.
"""
import asyncio
import base64
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from opentelemetry import trace as otel
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode
from arize.otel import register, Endpoint

logger = logging.getLogger(__name__)

_provider = None
_tracer = None


# --------------------------------------------------------------------------- #
# Setup (call once at app startup)
# --------------------------------------------------------------------------- #
def setup_tracing():
    """TRACING_PROVIDER=arize (default) | langfuse | none. Returns the tracer provider or None."""
    global _provider, _tracer
    if _provider:
        return _provider

    which = os.getenv("TRACING_PROVIDER", "arize").lower()
    try:
        if which == "arize":
            from arize.otel import register
            from openinference.instrumentation import OITracer, TraceConfig

            ARIZE_API_KEY = os.getenv("ARIZE_SECRET_KEY")
            ARIZE_SPACE_ID = os.getenv("ARIZE_SPACE_ID")
            ARIZE_PROJECT_NAME = os.getenv("ARIZE_PROJECT_NAME")

            if not (ARIZE_SPACE_ID and ARIZE_API_KEY):
                logger.warning("Arize env vars not set, tracing disabled")
                return None
            # batch=True: export on a background thread. The default exports synchronously
            # inside span.end(), i.e. a network call on your audio event loop.
            _provider = register(
                        space_id = ARIZE_SPACE_ID,
                        api_key = ARIZE_API_KEY,
                        project_name = ARIZE_PROJECT_NAME, # name this to whatever you would like
                        endpoint = Endpoint.ARIZE_EUROPE,
                        batch=True
                        )
            _tracer = OITracer(_provider.get_tracer("agent-platform"), config=TraceConfig())

        elif which == "langfuse":
            # Langfuse ingests OTLP at <host>/api/public/otel (verify against their docs).
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            pk, sk, host = (os.getenv(k) for k in
                            ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"))
            if not all([pk, sk, host]):
                logger.warning("Langfuse env vars not set, tracing disabled")
                return None
            auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
            _provider = TracerProvider()
            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
                endpoint=f"{host.rstrip('/')}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {auth}"},
            )))
            _tracer = _provider.get_tracer("voice-agent")
        else:
            return None
        logger.info(f"{which} tracing initialized")
    except Exception as e:
        logger.error(f"Failed to initialize {which} tracing: {e}")
        _provider = _tracer = None
    return _provider


def get_tracer():
    """Real tracer when configured, otherwise OTel's built-in no-op tracer (everything still works)."""
    return _tracer or otel.get_tracer("agent-platform")

def get_provider():
    return _provider or setup_tracing()

def _prim(v):
    return v if isinstance(v, (str, int, float, bool)) else json.dumps(v, default=str)


def _attrs(d: dict) -> dict:
    return {k: _prim(v) for k, v in d.items() if v is not None}


def _wall_ns(at: Optional[float]) -> Optional[int]:
    """Convert a time.perf_counter() timestamp to epoch ns, so spans can be backdated."""
    return None if at is None else time.time_ns() - int((time.perf_counter() - at) * 1e9)


# --------------------------------------------------------------------------- #
# ConversationTrace Delete this in Refactor Phase
# --------------------------------------------------------------------------- #
class ConversationTrace:
    def __init__(self, session_id: str, user_id: Optional[str] = None, metadata: Optional[dict] = None):
        self.tracer = get_tracer()
        self.session_id = session_id
        self.trace_id = uuid.uuid4().hex
        extra = {"user.id": user_id, **{f"metadata.{k}": v for k, v in (metadata or {}).items()}}
        # Context() = empty context, so the conversation is always a fresh root trace
        self._conv = self.tracer.start_span("conversation", context=Context(),
                                            attributes=self._a("AGENT", extra))
        self._conv_ctx = otel.set_span_in_context(self._conv)
        self._turn = None
        self._turn_ctx = None
        self.turn_index = 0
        self._closed = False
        self._barge_at: Optional[float] = None
        self._conv_counts: dict[str, int] = {}
        self._reset_turn_state()

    def _reset_turn_state(self):
        self._marks: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._indexes: dict[str, int] = {}
        self._user_text = ""
        self._generated: list[str] = []
        self._synthesized: list[str] = []
        self._tts_pending = 0
        self._bot_speaking = False
        self._needs_llm = True

    def _a(self, kind: str, extra: Optional[dict] = None) -> dict:
        return _attrs({"openinference.span.kind": kind, "session.id": self.session_id, **(extra or {})})

    # ---- creating spans -------------------------------------------------- #
    def ctx(self) -> Context:
        """Parent context: the open turn, else the conversation."""
        return self._turn_ctx or self._conv_ctx

    def begin(self, name: str, kind: str = "CHAIN", attrs: Optional[dict] = None):
        """Unmanaged span for code that yields (async generators). YOU must call .end()."""
        return self.tracer.start_span(name, context=self.ctx(), attributes=self._a(kind, attrs))

    @contextmanager
    def span(self, name: str, kind: str = "CHAIN", attrs: Optional[dict] = None):
        """
        Task-scoped span (don't hold it across a `yield` in an async generator; use begin()).
        Sets OK/ERROR explicitly, as the Arize docs require. Barge-in (CancelledError) is
        recorded as metadata.interrupted, not as an error.
        """
        with self.tracer.start_as_current_span(
            name, context=self.ctx(), attributes=self._a(kind, attrs),
            record_exception=False, set_status_on_exception=False,
        ) as s:
            try:
                yield s
            except asyncio.CancelledError:
                s.set_attribute("metadata.interrupted", True)
                s.set_status(Status(StatusCode.OK))
                raise
            except Exception as e:
                s.record_exception(e)
                s.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            else:
                s.set_status(Status(StatusCode.OK))

    # ---- marks / counters ------------------------------------------------ #
    def mark(self, name: str, at: Optional[float] = None) -> None:
        """First occurrence wins. `at` = time.perf_counter() value, for backdating."""
        if self._turn:
            self._marks.setdefault(name, at if at is not None else time.perf_counter())

    def _ms(self, a: str, b: str) -> Optional[float]:
        if a in self._marks and b in self._marks:
            return round((self._marks[b] - self._marks[a]) * 1000, 1)
        return None

    def count(self, key: str, n: int = 1) -> None:
        d = self._counts if self._turn else self._conv_counts
        d[key] = d.get(key, 0) + n

    def next_index(self, kind: str) -> int:
        i = self._indexes.get(kind, 0)
        self._indexes[kind] = i + 1
        return i

    def set_user_text(self, text: str) -> None:
        self._user_text = text

    def add_generated(self, text: str) -> None:
        self._generated.append(text)

    # ---- turn lifecycle -------------------------------------------------- #
    def _open_turn(self, kind: str, at: Optional[float] = None) -> None:
        self.turn_index += 1
        self._reset_turn_state()
        self._needs_llm = kind == "user"
        self._turn = self.tracer.start_span(
            f"turn_{self.turn_index}", context=self._conv_ctx,
            attributes=self._a("CHAIN", {"metadata.turn_index": self.turn_index, "metadata.turn_type": kind}),
            start_time=_wall_ns(at),
        )
        self._turn_ctx = otel.set_span_in_context(self._turn, self._conv_ctx)
        self.mark("turn_start", at)

    def start_agent_turn(self, kind: str) -> None:
        """Agent speaks first (WarmUp greeting, 'are you still there?')."""
        if not self._turn:
            self._open_turn(kind)

    def on_user_started(self, at: Optional[float] = None) -> None:
        """VAD confirmed speech (StartSpeaking). Closes any open turn, as interrupted if the agent had started."""
        if self._turn:
            agent_started = "llm_request" in self._marks or "tts_request" in self._marks
            if agent_started:
                self._barge_at = time.perf_counter()
            self.end_turn("barge_in" if agent_started else "superseded", interrupted=agent_started)
        self._open_turn("user", at)
        self.mark("user_speech_start", at)

    def on_user_stopped(self, at: Optional[float] = None, **vad_meta) -> None:
        """VAD confirmed end of speech (EndSpeaking). `at` = backdated real end of speech."""
        if not self._turn:
            return
        self.mark("user_speech_end", at)
        self.mark("vad_end_detected")
        s = self.tracer.start_span(
            "vad", context=self._turn_ctx,
            attributes=self._a("CHAIN", {
                **{f"metadata.{k}": v for k, v in vad_meta.items()},
                "metadata.hangover_ms": self._ms("user_speech_end", "vad_end_detected"),
            }),
            start_time=_wall_ns(self._marks.get("user_speech_start")),
        )
        s.set_status(Status(StatusCode.OK))
        s.end()  # real bar: first confirmed speech -> VAD decision

    def on_audio_purged(self) -> None:
        """Call right after output_track.purge(): how long the agent kept talking after barge-in."""
        if self._barge_at is not None and self._turn:
            self._turn.set_attribute("metadata.prev_turn_audio_stop_ms",
                                     round((time.perf_counter() - self._barge_at) * 1000, 1))
        self._barge_at = None
        self._tts_pending = 0

    # ---- agent side ------------------------------------------------------ #
    def on_llm_done(self) -> None:
        self.mark("llm_done")
        self._maybe_finish()

    def tts_enqueued(self, text: str) -> None:
        self._tts_pending += 1
        self._synthesized.append(text)

    def tts_sentence_done(self) -> None:
        self._tts_pending = max(0, self._tts_pending - 1)

    def on_bot_started(self) -> None:
        """Wire to output_track.on_audio_start: first real frame leaving for the peer."""
        self._bot_speaking = True
        self.mark("tts_first_audio")

    def on_bot_idle(self) -> None:
        """Wire to output_track.on_audio_idle: playback queue ran dry."""
        self._bot_speaking = False
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if (self._turn
                and (not self._needs_llm or "llm_done" in self._marks)
                and "tts_first_audio" in self._marks
                and self._tts_pending == 0
                and not self._bot_speaking):
            self.end_turn("completed")

    # ---- closing --------------------------------------------------------- #
    def end_turn(self, reason: str = "completed", interrupted: bool = False) -> None:
        t = self._turn
        if not t:
            return
        self._turn = self._turn_ctx = None
        metrics = {
            "response_latency_ms": self._ms("user_speech_end", "tts_first_audio"),  # what the user feels
            "stt_ms": self._ms("vad_end_detected", "stt_final"),
            "llm_ttft_ms": self._ms("llm_request", "llm_first_token"),             # includes tool time on tool turns
            "llm_to_tts_ms": self._ms("llm_first_token", "tts_request"),
            "tts_ttfb_ms": self._ms("tts_request", "tts_first_audio"),
            "turn_to_first_audio_ms": self._ms("turn_start", "tts_first_audio"),   # agent-initiated turns
            "end_reason": reason,
            "interrupted": interrupted,
            **self._counts,
        }
        t.set_attributes(_attrs({
            **{f"metadata.{k}": v for k, v in metrics.items()},
            "input.value": self._user_text or None,
            "output.value": " ".join(self._generated) or None,
            "metadata.agent_sent_to_tts": " ".join(self._synthesized) or None,
        }))
        t.set_status(Status(StatusCode.OK))
        t.end()

    def close(self) -> None:
        """Call once at session teardown. force_flush blocks, so use asyncio.to_thread in async code."""
        if self._closed:
            return
        self._closed = True
        self.end_turn("session_end")
        self._conv.set_attributes(_attrs({
            "metadata.turns": self.turn_index,
            **{f"metadata.{k}": v for k, v in self._conv_counts.items()},
        }))
        self._conv.set_status(Status(StatusCode.OK))
        self._conv.end()
        if _provider:
            try:
                _provider.force_flush()
            except Exception as e:
                logger.warning(f"[Tracing] flush failed: {e}")