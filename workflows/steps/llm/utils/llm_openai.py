from workflows.steps.llm.types import LLMEvent
from typing import Optional, AsyncGenerator
from openai import AsyncOpenAI
import httpx, json, logging, asyncio, time
from urllib.parse import urlparse

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

# Keys accepted by client.chat.completions.create(), beyond model/messages/tools/stream
# which are already handled explicitly above.
VALID_CHAT_COMPLETION_KEYS = {
    "temperature",
    "top_p",
    "n",
    "seed",
    "max_tokens",              # legacy, some models still want this
    "max_completion_tokens",   # newer models (o-series, gpt-5 family)
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "tool_choice",
    "parallel_tool_calls",
    "response_format",
    "reasoning_effort",
    "stream_options",
    "logprobs",
    "top_logprobs",
    "user",
    "metadata",
    "modalities",
    "audio",
    "store",
}


def extract_valid_llm_config(llm_config: Optional[dict]) -> dict:
    """
    Filters llm_config down to keys accepted by chat.completions.create(),
    dropping anything unrecognized (and logging what got dropped) instead of
    blindly merging and letting the OpenAI SDK raise a confusing TypeError.
    """
    if not llm_config:
        return {}

    valid = {k: v for k, v in llm_config.items() if k in VALID_CHAT_COMPLETION_KEYS}
    dropped = set(llm_config.keys()) - VALID_CHAT_COMPLETION_KEYS

    if dropped:
        logger.warning(f"[LLM Engine] Ignoring unsupported llm_config keys: {sorted(dropped)}")

    return valid

'''
    This layer send all info the llm needs, prompt,tools, messages, etc 
    and handle all type of response's tokens, content, tools_call, reasoning, etc.
    How to setup and handle the response is tight coupling with the LLM Provider.
    
    token event will be yield for each token.
    tool_call event will be yield when the llm finish the response.?
    finish event will be yield when the llm finish the response.
    reasoning event will be yield when the llm finish the response.
    coding_block event will be yield when the llm finish the response.
    ttft event will be yield when the llm finish the response.
    token_usage event will be yield when the llm finish the response.
'''
async def call_llm_stream_openai(
    messages: Optional[list] = None,
    tools: Optional[list] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    tracing_data: Optional[dict] = None,
    model: Optional[str] = None,
    prompt: Optional[str] = None,
    provider_url: str = None,
    api_key: str = None,
    llm_config: Optional[dict] = None,
) -> AsyncGenerator[LLMEvent, None]:
    """
    Async streaming using AsyncOpenAI client style.
    Yields LLMEvent with event: 'token' | 'tool_call' | 'finish' | 'reasoning'.

    Also Yields custom events: coding_block, ttft (time to first token), token_usage, etc.
    Handle more as needed.
    """
    own_http_client = False
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
        own_http_client = True
    
    if prompt and (messages or tools):
        logger.warning("[LLM Engine] Prompt and messages both cannot be set")
        # TODO: Handle this case do something more stream like raise a exception or yield a error event
        raise Exception("[LLM Engine] Prompt and messages or tools both cannot be set togheter")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=provider_url,
        http_client=http_client
    )
    kwargs = {
        "model": model,
        "stream": True,
    }

    if prompt:
        kwargs["message"] = [{"role": "user", "content": prompt}]
    
    if messages:
        kwargs["messages"] = messages    

    if tools and len(tools) > 0:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    # Extract the config from database to LLM Expected
    if llm_config:
        kwargs.update(extract_valid_llm_config(llm_config))

    # --- MINIMAL TRACING HOOKS ---
    tracer = tracing_data.get("tracer") if tracing_data else None
    parent_turn = tracing_data.get("parent_span") if tracing_data else None
    parent_ctx = set_span_in_context(parent_turn) 
    
    span = tracer.start_span(
        "llm.stream",
        context=parent_ctx,
        attributes={
            SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
            SpanAttributes.LLM_REQUEST_MODEL_NAME: model or "unknown",
            SpanAttributes.LLM_SYSTEM: OpenInferenceLLMSystemValues.OPENAI.value,  # OpenAI-compatible API 
            LLMStreamAttributes.BASE_URL_HOST: urlparse(provider_url).hostname or provider_url if provider_url else "",
        },
    )
    

    accumulated_text = "" # Only for debug pourposes yield individual token
    # Track accumulated tool calls by their delta index
    accumulated_tools = {}
    final_reason = "unknown"

    ttft_ms: float | None = None

    try:
        t0 = time.perf_counter()
        async with await client.chat.completions.create(**kwargs) as stream:
            async for chunk in stream:
                logger.debug(f"[raw_chunk] got chunk: {chunk}")  
                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason

                # ADD THIS:
                logger.debug(f"[raw_chunk] finish={finish_reason} content={repr(getattr(delta, 'content', None))} tool_calls={getattr(delta, 'tool_calls', None)}")

                # Extract content and tool, calls, could be reasoning, etc from the delta
                token = getattr(delta, "content", None)
                tool_calls = getattr(delta, "tool_calls", None) or []

                if token:
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t0) * 1000
                        span.add_event(LLMStreamAttributes.TTFT_MS)
                    accumulated_text += token
                    
                    yield {"event": "token", "data": token}

                for tc in tool_calls:

                    idx = tc.index
                    
                    # 1. Initialize the tool slot if it's the first time seeing this index
                    if idx not in accumulated_tools:
                        accumulated_tools[idx] = {
                            "id": tc.id, # Sent in the first chunk for this index
                            "name": tc.function.name if tc.function else "",
                            "arguments": ""
                        }
                    
                    # 2. Update properties if they are sent in subsequent chunks
                    if tc.id and not accumulated_tools[idx]["id"]:
                        accumulated_tools[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        accumulated_tools[idx]["name"] = tc.function.name
                        
                    # 3. Accumulate the streamed JSON arguments string
                    if tc.function and tc.function.arguments:
                        accumulated_tools[idx]["arguments"] += tc.function.arguments


                    yield   {
                                "event": "tool_call",
                                "data": {
                                    "index": tc.index,
                                    "id": tc.id,
                                    "function": {
                                        "name": tc.function.name if tc.function else "",
                                        "arguments": tc.function.arguments if tc.function else "",
                                    },
                                }
                            }

                if finish_reason:
                    
                                     
                    for idx, tool in sorted(accumulated_tools.items()):
                        span.set_attribute(ToolCallAttributes.TOOL_CALL_FUNCTION_NAME, tool["name"])
                        span.set_attribute(ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON, tool["arguments"])
                                    
                    span.set_attribute(SpanAttributes.LLM_FINISH_REASON, finish_reason) 
                             

                    yield {"event": "finish", "data": finish_reason}
        
        
    except (asyncio.CancelledError, GeneratorExit):
        # User barged in (task cancelled) or the consumer stopped iterating.
        logger.info("[LLM Engine] Stream cut short by user barge-in.")
        interrupted = True
        raise  # must re-raise for proper pipeline task cleanup
    except Exception as e:
        logger.error(f"Error calling LLM: {e}")
        failed = True
        span.record_exception(e)
        span.set_status(Status(StatusCode.ERROR, str(e)))
        yield {"event": "error", "data": str(e)}    
    finally:
        if own_http_client:
            await http_client.aclose()
        if span:
            span.end()