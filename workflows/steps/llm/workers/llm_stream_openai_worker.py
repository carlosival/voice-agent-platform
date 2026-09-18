import asyncio
from typing import Optional
from httpx import AsyncClient
from workflows.utils.memory import IMemory
from workflows.utils.tools.tool_types import ToolCallChunk
from workflows.utils.tools.tool_executor import execute_tool
from workflows.utils.context.message_context_builder import build_chat_messages
from smolagents.models import get_tool_json_schema
from ..utils import call_llm_stream_openai
from workflows.steps.llm.types import LLMEvent
from workflows.signals import EndOfStream
import logging, re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SENTENCE_ENDS = {",", ".",  "\n", "\r", "\n\n", "\r\n", "!", "?", "…", "。"}
BAD_PATTERNS = [
            r"\*\*.*?\*\*",          # Markdown bold
            r"\#+ .*",               # Markdown headers
            r"<[^>]*>",              # XML/HTML tags (like <function=...></function>)
            r"\[.*?\]",              # Bracketed text annotations
            r"`{1,3}.*?`{1,3}",      # Code snippets
        ]

def sanitize_sentence(text: str) -> str:
        """
        Cleans up a complete sentence before handing it to the TTS engine.
        """
        if not text:
            return ""
        
        # 1. Remove systemic noise, tags, and markdown formatting
        cleaned = text
        for pattern in BAD_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned)
            
        # 2. Normalize text numbers or symbols that sound weird when spoken
        cleaned = cleaned.replace("%", " por ciento")
        cleaned = cleaned.replace("&", " y ")
        
        # 3. Clean up accidental double spaces left behind by removals
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        
        return cleaned


'''
 THIS IS A WORKER FOR STREAMING LLM RESPONSES with a TOOL CALLING LOOP
    ADD MORE LOGIC LIKE:
    - EVALS
    - TOKEN/COST LIMITS
    - RETRY LOGIC
    - ETC
'''

async def call_llm_stream_openai_worker(
    text: str,
    memory: IMemory, # The last message is the user input already included in memory
    tools: Optional[list] = None,
    http_client: Optional[AsyncClient] = None,
    tracing_data: Optional[dict] = None, # {"tracer": tracer, "trace_id": trace_id, "parent_span_id": parent_span_id}
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    provider_url: str = None,
    api_key: str = None,
    llm_config: Optional[dict] = {},
    queue: asyncio.Queue = None
    ):
        sentence_queue = queue
        token_count = 0
        buffer = ""


        # ─────────────────────────────────────────────
        # Moment of apply all SYNC PRE-PROCESSING HOOKS before build the prompt
        # ─────────────────────────────────────────────

        ''' 
            # TODO: Implement this
            # hooks is a list of sync functions that will be applied to the user text before building the prompt
            # hooks = llm_config.get("hooks", [])
            pro_text = text
            for hook in hooks:
                pro_text = hook(pro_text)
        '''

        # ─────────────────────────────────────────────
        # END OF SYNC PRE-PROCESSING HOOKS
        # ─────────────────────────────────────────────

        # ─────────────────────────────────────────────
        # Build the prompt, messages and tools schema according to the LLM provider
        # ─────────────────────────────────────────────

        # Build messages from history
        messages = await build_chat_messages(memory, system_prompt, text)
        
        # Build tools schema safely (Tool Schema depends on the LLM provider) 
        # Refactor: Refactor to a function tool_schema_builder
        if tools:
            tools_schema = [get_tool_json_schema(t) for t in tools.values()]
            print(f"DEBUG: tools_schema value is {tools_schema}")
        else:
            tools_schema = None

        
        tool_calls_acc: dict[int, dict] = {}
        full_response = ""
        try:
            async for event in call_llm_stream_openai(
                messages=messages, 
                tools=tools_schema if tools_schema else None, 
                http_client=http_client, 
                tracing_data=tracing_data,
                model=model,
                provider_url=provider_url,
                api_key=api_key,
                llm_config=llm_config
                ):
                
                event_type = event["event"]
                data = event["data"]
                
                # ─────────────────────────────────────────────
                # TOKEN STREAM
                # ─────────────────────────────────────────────
                if event_type == "token":

                    # Post Processing Hooks per valuable chunks of data.
                    # You can work with allucination mesuare here, per valuable chunks of data.
                    buffer   += data
                    logger.info(f"[llm_stream] token #{token_count}: {repr(data)}")

                    if buffer.rstrip() and buffer.rstrip()[-1] in SENTENCE_ENDS:
                            logger.info(f"[llm_stream] Flushing sentence: '{buffer.strip()}'")
                            raw_sentence = buffer.strip()
                
                            # Apply the broad voice sanitization
                            clean_sentence = sanitize_sentence(raw_sentence)
                            
                            # Crucial Voice Safety Check: 
                            # If the LLM only outputted an XML tag (like <function=end_conversation></function>),
                            # the clean_sentence will now be empty "". We must NOT queue empty text to TTS.
                            if clean_sentence:
                                logger.info(f"[llm_stream] Flushing sentence: '{clean_sentence}' (Raw: '{raw_sentence}')")
                                sentence_queue.put_nowait(clean_sentence)
                            else:
                                logger.warning(f"[llm_stream] Dropped hallucinated syntax chunk: '{raw_sentence}'")
                            
                            # Always reset the buffer regardless
                            full_response += buffer
                            buffer = ""
                # ─────────────────────────────────────────────
                # TOOL CALL REDUCTION ONLY
                # ALL SYNC POSTPROCESSING HOOKS MUST BE DONE AFTER CORRESPONDING FINISH EVENT
                # ─────────────────────────────────────────────
                elif event_type == "tool_call":

                    tc = data

                    idx = tc["index"]

                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }

                    if tc.get("id"):
                        tool_calls_acc[idx]["id"] = tc["id"]

                    if fn := tc.get("function", {}):

                        tool_calls_acc[idx]["name"] += fn.get("name", "")
                        arg_chunk = fn.get("arguments", "")
                        if arg_chunk and arg_chunk != "null":   # ← skip "null" sentinel
                            tool_calls_acc[idx]["arguments"] += arg_chunk   
                # ─────────────────────────────────────────────
                # FINISH HANDLING EXECUTE POSTPROCESSING SYNC HOOKS AFTER ALL TOKENS ARE RECEIVED
                # ─────────────────────────────────────────────
                elif event_type == "finish":

                    reason = data

                    logger.info(
                        f"[llm_stream] finish reason: {reason}"
                    )

                    # ─────────────────────────────────────────────
                    # Normal Finish — model completed naturally, no tools requested
                    # ─────────────────────────────────────────────
                    if reason == "stop":
                        # Nothing left to accumulate — any trailing partial sentence in
                        # `buffer` still gets flushed after the loop ends (existing code).
                        # This is the spot for post-processing hooks that should run
                        # once per full assistant turn (not per tool-call round):
                        #   - append the assistant's full text response to memory/history
                        #   - run evals / logging / cost tracking
                        #   - signal "end of assistant turn" downstream if your consumer needs it
                        logger.info("[llm_stream] Normal finish — assistant turn complete.")
                        if buffer.strip():
                            logger.info(f"[llm_stream] Flushing remainder: '{buffer.strip()}'")
                            buff_stripe = buffer.strip()
                            full_response += buff_stripe
                            sentence_queue.put_nowait(buff_stripe)

                        sentence_queue.put_nowait(None) # End of Assitant Turn

                    elif reason == "length":
                        logger.warning("[llm_stream] Response truncated due to max token limit.")
                        # Consider still flushing buffer / notifying downstream that output was cut off

                    elif reason == "content_filter":
                        logger.warning("[llm_stream] Response stopped by content filter.")

                    # ─────────────────────────────────────────────
                    # TOOL CALL EXECUTION AND OTHER POSTPROCESSING HOOKS
                    # ─────────────────────────────────────────────
                    if reason == "tool_calls":

                        mapped_calls = [
                            ToolCallChunk(
                                id=acc["id"],
                                name=acc["name"],
                                arguments=acc["arguments"],
                            )
                            for acc in tool_calls_acc.values()
                        ]

                        logger.info(
                            f"[llm_stream] Reduced tool calls: {mapped_calls}"
                        )

                        # Could be sequential or parallel, as needed
                        # Parallel execution via asyncio.gather if async tools
                        results = await asyncio.gather(*[execute_tool(tc, tools) for tc in mapped_calls])
                        logger.info(f"[llm_stream] Tool call results: {results}")

                        if EndOfStream in [type(r.get("result", None)) for r in results]:
                            logger.info("[llm_stream] EndOfStream from tool result. Finishing conversation.")
                            sentence_queue.put_nowait(EndOfStream())
                            break

                        zip_tc_results = zip(mapped_calls, results, strict=True)
                        sentence_queue.put_nowait(list(zip_tc_results))

                        # Process tool call results according to bussiness logic
                        # Add tools calls and results to message history
                        await message_history.add_tools_calls(mapped_calls)
                        await message_history.add_tools_results(results)
                        
                        # Add results to processing queue all, some or none depending of the busssiness logic
                        for tc_result in results:
                            result = tc_result.get("result", None)
                            if result is not None:
                                if isinstance(result, str):
                                    sentence_queue.put_nowait(result)
                                if isinstance(result, EOStream):
                                    sentence_queue.clear()
                                    sentence_queue.put_nowait(result)
                                    break
                                    

                        #Signal to the consumer that the tool calls have been processed
                        sentence_queue.put_nowait(mapped_calls) 

                        tool_calls_acc.clear()
                
            

        except asyncio.CancelledError as cancel_error:
            logger.info("[llm_stream] Cancelled by signal.")
            sentence_queue.put_nowait(cancel_error)
        except Exception as e:
            logger.error(f"[llm_stream] Unexpected error in worker: {e}")
            sentence_queue.put_nowait(e)
        
