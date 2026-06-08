import os
import asyncio
import httpx
from httpx import AsyncClient
from typing import AsyncGenerator
import json
import logging
from typing import Optional 
from openai import AsyncOpenAI
logger = logging.getLogger(__name__)


LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://ollama:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
print(LLM_BASE_URL)
LLM_MODEL    = os.getenv("LLM_MODEL", "llama3.1:8b")
print(LLM_MODEL)
# ─── LLM Call ────────────────────────────────────────────────────────────────


async def call_llm_stream_openai(
    messages: list,
    tools: Optional[list] = None,
    http_client: Optional[AsyncClient] = None,
    tracing_data: Optional[dict] = None
) -> AsyncGenerator[dict, None]:
    """
    Async streaming using AsyncOpenAI client style.
    Yields dicts with type: 'token' | 'tool_call' | 'finish'.
    """
    if http_client is None:
        http_client = AsyncClient(timeout=30.0)
    client = AsyncOpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        http_client=http_client
    )
    kwargs = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    # --- MINIMAL TRACING HOOKS ---
    tracer = tracing_data.get("tracer") if tracing_data else None
    trace_id = tracing_data.get("trace_id") if tracing_data else None
    parent_span_id = tracing_data.get("parent_span_id") if tracing_data else None
    span = None

    if tracer and trace_id:

        # Build a complete input object containing both messages and available tools
        trace_input = {
            "messages": messages
        }
        if tools:
            trace_input["tools"] = tools  # <-- Pass the available tools here

        # Refactor this to get more data like TTFT, Token Usage, etc.
        span = tracer.start_observation(
            name="llm_stream_generation",
            as_type="generation",
            model=LLM_MODEL,
            input=trace_input,
            trace_context={"trace_id": trace_id, "parent_span_id": parent_span_id}  # Links cleanly as a child under the WebRTC session
        )

    accumulated_text = ""
    # Track accumulated tool calls by their delta index
    accumulated_tools = {}
    final_reason = "unknown"

    try:
        async with await client.chat.completions.create(**kwargs) as stream:
            async for chunk in stream:
                logger.info(f"[raw_chunk] got chunk: {chunk}")  
                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason

                # ADD THIS:
                logger.info(f"[raw_chunk] finish={finish_reason} content={repr(getattr(delta, 'content', None))} tool_calls={getattr(delta, 'tool_calls', None)}")

                token = getattr(delta, "content", None)
                tool_calls = getattr(delta, "tool_calls", None) or []

                if token:
                    accumulated_text += token
                    yield {"type": "token", "data": token}

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
                                "type": "tool_call",
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
                    yield {"type": "finish", "data": finish_reason}
        
        # Convert our tracking dict back into a clean list for the tracer
        final_tools = [tool for idx, tool in sorted(accumulated_tools.items())]

        # Stream ended cleanly -> update output data
        if span:
            span.update(output={"text": accumulated_text, "tool_calls": final_tools, "finish_reason": final_reason})
    except CancelledError:
        # User barged in and interrupted the stream
        logger.info("[LLM Engine] Stream cut short by user barge-in.")
        if span:
            span.update(
                level="WARNING",
                status_message="Stream dropped due to user interruption event.",
                output={"text": accumulated_text + "... [Cut Off]","tool_calls": final_tools, "finish_reason": "barge_in"}
            )
        raise  # Must re-raise CancelledError for proper pipeline task cleanup
    except Exception as e:
        logger.error(f"Error calling LLM: {e}")
        if span:
            span.update(level="ERROR", status_message=str(e))
    finally:
        if span:
            span.end()
        
        




async def call_llm(http_client: AsyncClient, messages: list) -> str:
    """Call vLLM OpenAI-compatible chat endpoint."""
    resp = await http_client.post(
        f"{LLM_BASE_URL}/chat/completions",
        json={
            "model":       LLM_MODEL,
            "messages":    messages,
            "temperature": 0,
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()

# Stream call

async def call_llm_stream_httpx(
    http_client: AsyncClient,
    messages: list,
    tools: Optional[list] = None
) -> AsyncGenerator[str, None]:
    """
    Stream tokens from Ollama/vLLM as they are generated.
    Yields one text chunk per token — use for lowest latency TTS pipeline.

    Usage:
        async for chunk in call_llm_stream(client, "hello"):
            print(chunk, end="", flush=True)
    """

    payload = {
        "model":       LLM_MODEL,
        "messages":    messages,
        "temperature": 0,
        "stream":      True,        # ← enables SSE streaming

    }
    
    if tools: 
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    
    

    async with http_client.stream(
        "POST",
        f"{LLM_BASE_URL}/chat/completions",
        json=payload
    ) as resp:
        resp.raise_for_status()

        async for line in resp.aiter_lines():

            logger.info(f"[call_llm_stream] raw chunk: {repr(line)}")

            if not line.startswith("data:"):
                continue

            payload = line[len("data:"):].strip()

            if payload == "[DONE]":
                break

            try:
                chunk = json.loads(payload)

                choice = chunk["choices"][0]
                delta = choice.get("delta", {})

                token = delta.get("content", None)
                tool_calls = delta.get("tool_calls", [])
                finish_reason = choice.get("finish_reason", None)

                if token:
                    yield {
                        "type": "token",
                        "data": token
                    }

                for tc in tool_calls:
                    yield {
                        "type": "tool_call",
                        "data": tc
                    }

                if finish_reason:
                    yield {
                        "type": "finish",
                        "data": finish_reason
                    }

            except (json.JSONDecodeError, KeyError, IndexError):
                continue


# ─── Test ─────────────────────────────────────────────────────────────────────
# Uses this command to test:
# docker exec -it -e LLM_BASE_URL="http://ollama:11434/v1" -e LLM_MODEL="llama3.1:8b" fastapi python3 utils/call_llm.py



async def _test():
    prompt = "In a few words, what's the meaning of life?"
    print(f"URL:      {LLM_BASE_URL}")
    print(f"Model:    {LLM_MODEL}")
    print(f"Prompt:   {prompt}")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt}
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:  # ✅ creates its own client
        
        # Test 1 — full response
        print("── Test 1: Full response ──")
        response = await call_llm(client, messages)
        print(f"Response: {response}\n")

        # Test 2 — streaming tokens
        print("── Test 2: Streaming tokens ──")
        print("Response: ", end="", flush=True)
        async for token in call_llm_stream(client, messages):
            print(token, end="", flush=True)
        print("\n")

    print(f"Response: {response}")


if __name__ == "__main__":
    asyncio.run(_test())                                    # ✅ runs async correctly