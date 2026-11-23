import json, logging, httpx, os
from typing import Optional, Any
from workflows.utils.helpers import json_parser, merge_dict
from workflows.utils.memory import IMemory
from workflows.utils.context import build_chat_messages, _extract_context_and_user_text
from jsonschema import Draft202012Validator, ValidationError
from urllib.parse import urlparse
from datetime import datetime
from zoneinfo import ZoneInfo

from opentelemetry.trace import Status, StatusCode, set_span_in_context
from openinference.semconv.trace import (
    ToolCallAttributes,
    SpanAttributes,
    AudioAttributes,
    OpenInferenceSpanKindValues,
    OpenInferenceMimeTypeValues,
    OpenInferenceLLMSystemValues
)

logger = logging.getLogger(__name__)

# No semconv constants for these, keep them in one place (same idea as STTAttributes).
class LLMStreamAttributes:
    TTFT_MS = "llm.stream.ttft_ms"
    FINISH_REASON = "llm.stream.finish_reason"
    INTERRUPTED = "llm.stream.interrupted"
    BASE_URL_HOST = "llm.stream.base_url_host"


STATE_EXTRACTION_PROMPT = """
You are a deterministic conversation state extraction engine. Your output is already constrained to the target JSON schema—focus solely on accurate semantic extraction and state updates.

### Context
Reference date/time (for resolving relative dates like "today", "tomorrow", "mañana", or "next Tuesday"):
{reference_datetime}

Current state:
{current_state}

Recent conversation (oldest to newest, for context only):
{recent_turns}

Latest user utterance (extract updates from THIS turn):
{user_text}

### Extraction Rules
1. **Focus on the Latest Utterance:** Populate or update fields ONLY when clearly supported by `Latest user utterance`. Use `Recent conversation` solely to understand what question is being answered, merge split/spelled-out inputs, or resolve short replies ("yes", "sí", "no", "the second one"). Never guess or infer unmentioned facts.
2. **Preserve Existing State:** Copy all existing values from `Current state` unchanged unless explicitly updated, completed, or cleared in `Latest user utterance`.
3. **Corrections & Clearing:**
   - If the user corrects a prior value (e.g., "actually...", "no, wait...", "mejor dicho...", "perdón, quería decir..."), overwrite the old value with the corrected one.
   - If the user explicitly retracts or cancels a field without providing a replacement, set that field to `null` (when allowed by the schema).
4. **Multilingual Handling:**
   - The conversation may be in any language (or switch languages mid-call). Understand intent in the caller's language.
   - Map categorical fields to the exact English `enum` values defined in the JSON schema.
   - Preserve free-text values (names, street addresses, notes) in the caller's original language—do not translate them.
5. **Spoken & STT Normalization:**
   - Convert spoken symbols and numbers into standard characters regardless of language (e.g., "at" / "arroba" / "arobase" -> "@", "dot" / "punto" / "point" -> ".", "underscore" / "guion bajo" -> "_", "dash" / "guion" -> "-", spelled digits -> compact numbers).
   - Format dates strictly as ISO `YYYY-MM-DD` (interpreting ambiguous numeric dates using `DD/MM/YYYY` day-first convention unless the schema description specifies otherwise) and times as 24-hour `HH:MM`.
   - Strip spaces and separators from IDs, postal codes, and phone numbers unless the schema specifies a delimiter.
6. **No-Op on Filler/Noise:** If `Latest user utterance` is filler ("uh", "este...", "un momento"), background noise, or too ambiguous to map cleanly to a schema field, return `Current state` unchanged.
"""

def get_reference_datetime(tz_name: str = "Europe/Madrid") -> str:
    now = datetime.now(ZoneInfo(tz_name))
    # Example: "2026-11-20T14:05:00+01:00 (Friday)"
    return f"{now.isoformat(timespec='seconds')} ({now.strftime('%A')})"

async def extractor(prompt: str,
                    schema: dict,
                    *,
                    http_client: Optional[httpx.AsyncClient],
                    provider_url: str,
                    api_key: str | None,
                    model: str,
                    temperature: float = 0.0,
                    timeout: float = 10.0,
                    tracing_data: Optional[dict] = None,
                    ) -> dict[str, Any]:


    

    own_http_client = http_client is None
    if own_http_client:
        http_client = httpx.AsyncClient(timeout=30.0)

    # Tracing is optional: fall back to the global tracer / current context
    tracer = (tracing_data or {}).get("tracer") or trace.get_tracer(__name__)
    parent_turn = (tracing_data or {}).get("parent_span")
    parent_ctx = set_span_in_context(parent_turn) if parent_turn else None

    host = (urlparse(provider_url).hostname if provider_url else None) or ""
    span = tracer.start_span(
        "llm.state.extractor",
        context=parent_ctx,
        attributes={
            SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
            SpanAttributes.LLM_REQUEST_MODEL_NAME: model or "unknown",
            SpanAttributes.LLM_SYSTEM: OpenInferenceLLMSystemValues.OPENAI.value,
            LLMStreamAttributes.BASE_URL_HOST: host,
        },
    )

    url = f"{provider_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "You are a conversation state extraction engine. "
                "Extract only information explicitly supported by the conversation. "
                "Do not invent values. Return valid structured data.")},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "stream": False,
        "reasoning_effort": "low",          # gpt-oss: keep latency down
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "conversation_state", "strict": True, "schema": schema},
        },
    }

    try:
        span.set_attribute(SpanAttributes.INPUT_VALUE, prompt)

        response = await http_client.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code >= 400:
            logger.error("State extractor HTTP %s: %s", response.status_code, response.text)
        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("Empty state extraction response")

        data = json.loads(content)
        Draft202012Validator(schema).validate(data)

        span.set_attribute(SpanAttributes.OUTPUT_VALUE, json.dumps(data, ensure_ascii=False))
        return {"updates": data}

    except Exception as e:
        # One place to log + mark the span, then propagate to the caller
        logger.exception("State extraction failed")
        span.record_exception(e)
        span.set_status(Status(StatusCode.ERROR, str(e)))
        raise

    finally:
        if own_http_client:
            await http_client.aclose()
        span.end()
   

async def extract_state_updates(
    user_text: str,
    memory: IMemory,
    current_state: dict,
    state_schema: dict,
    http_client: httpx.AsyncClient,
    provider_url: str,
    api_key: str | None,
    model: str,
    tracing_data: Optional[dict] = None, # {"tracer": tracer, "parent_span": turn_span}
    extractor: callable = extractor,
) -> dict:
    """
    extractor is a non-streaming structured-output provider adapter.
    It must return a validated JSON object.
    """

    # Build messages from history
    messages = await memory.get_messages()
    recent_turns, _ = _extract_context_and_user_text(messages)

    prompt = STATE_EXTRACTION_PROMPT.format(
        reference_datetime=get_reference_datetime("Europe/Madrid"),
        current_state=json.dumps(current_state, ensure_ascii=False),
        recent_turns=recent_turns,
        user_text=user_text,
    )

    result = await extractor(
        prompt=prompt,
        schema=state_schema,
        http_client=http_client,
        provider_url=provider_url,
        api_key=api_key,
        model=model,
        tracing_data=tracing_data,
    )

    delta = result.get("updates", {})
    logger.info(f"current state prior update{current_state}")
    merge_dict(current_state, delta) # Darke pattern merge_dict update current state in place
    logger.info(f"current state post update{current_state}")
    return delta

if __name__ == "__main__":
    
    # Refactor and test this.
    import asyncio
    import os

    logging.basicConfig(level=logging.INFO)

    # Standalone stubs if running this file directly without external memory/message helpers
    async def build_chat_messages(memory: Any, system_prompt: str = "", text: str = "") -> list[dict]:
        history = list(memory or [])
        if text:
            history.append({"role": "user", "content": text})
        return history

    def _extract_context_and_user_text(messages: list[dict], max_history_msgs: int = 6) -> tuple[str, str]:
        last_user_idx = next(
            (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
            None,
        )
        if last_user_idx is None:
            return "", ""
        latest_user_text = (messages[last_user_idx].get("content") or "").strip()
        prior_msgs = messages[max(0, last_user_idx - max_history_msgs):last_user_idx]
        formatted = "\n".join(f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in prior_msgs)
        return formatted, latest_user_text

    sample_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "caller_name": {"type": ["string", "null"], "description": "Caller's full name."},
            "email": {"type": ["string", "null"], "description": "Normalized email address."},
            "intent": {
                "type": ["string", "null"],
                "enum": ["appointment", "billing", "support", "other", None],
                "description": "Primary reason for calling.",
            },
            "preferred_date": {
                "type": ["string", "null"],
                "description": "Date in YYYY-MM-DD format.",
            },
        },
        "required": ["caller_name", "email", "intent", "preferred_date"],
    }

    async def _main() -> None:
        provider_url = os.getenv("LLM_PROVIDER_URL", "https://api.openai.com/v1")
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("LLM_EXTRACTION_MODEL", "gpt-4o-mini")

        state: dict[str, Any] = {
            "caller_name": None,
            "email": None,
            "intent": None,
            "preferred_date": None,
        }
        memory: list[dict[str, str]] = []

        turns = [
            "Hola, quiero pedir una cita para mañana.",
            "Me llamo Carlos Ruiz y mi correo es carlos punto ruiz arroba ejemplo punto es.",
            "Perdón, mejor dicho para el próximo lunes.",
        ]

        async with httpx.AsyncClient() as client:
            for turn in turns:
                print(f"\n--- User: {turn} ---")
                updated = await extract_state_updates(
                    user_text=turn,
                    memory=memory,
                    current_state=state,
                    state_schema=sample_schema,
                    http_client=client,
                    provider_url=provider_url,
                    api_key=api_key,
                    model=model,
                )
                memory.append({"role": "user", "content": turn})
                memory.append({"role": "assistant", "content": "Anotado, ¿algo más?"})
                print("Updated state:", json.dumps(state, indent=2, ensure_ascii=False))
                print("Returned delta:", json.dumps(updated, indent=2, ensure_ascii=False))

    asyncio.run(_main())