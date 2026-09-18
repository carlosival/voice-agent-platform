from typing import Dict, AsyncGenerator
from .workers import call_llm_stream_openai_worker, get_llm_provider_worker, get_provider_url
from workflows.signals import SignalFrame, WarmUp, AskUserStillThere, EndOfStream, StartSpeaking
from yaafpy.types import ExecContext
import logging 
import asyncio
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def llm_stream(
    source: AsyncGenerator,
    ctx:    ExecContext,
) -> AsyncGenerator[str, None]:
    """
    Decides based on the context what inference provider to use and calls the provider.
    """

    current_task = None
    sentence_queue = None  # ← initialize to None, not unbound
    http_client:     httpx.AsyncClient = ctx.shared_data["resources"]["http_client"]
    message_history: InMemoryMemory = ctx.shared_data["message_history"]
    tools: Dict[str, Tool] = ctx.shared_data.get("tools", {}) # Dict[str, Tool] smolagent
    system_prompt = ctx.shared_data.get("system_prompt", "")
    provider_name = ctx.shared_data["llm_config"]["engine"]
    provider_url = get_provider_url(provider_name)
    model = ctx.shared_data["llm_config"]["model"]
    llm_config = ctx.shared_data["llm_config"]
    api_key = ctx.shared_data["llm_api_key"]
    tracer = ctx.shared_data["resources"]["tracer"]
    trace_id = ctx.shared_data["trace_context"]["trace_id"]
    parent_span_id = ctx.shared_data["trace_context"]["parent_span_id"]
    timeout_limit = 5.0
    
    ''' Deprecated Delete this function it is in worker dir
    async def llm_stream_worker(text, sentence_queue):
        token_count = 0
        buffer = ""
        # Build messages from history
        messages = build_chat_messages(await message_history.get_messages(), system_prompt)
        # Build schema safely
        if tools:
            tools_schema = [get_tool_json_schema(t) for t in tools.values()]
            print(f"DEBUG: tools_schema value is {tools_schema}")
        else:
            tools_schema = None

        
        tool_calls_acc: dict[int, dict] = {}
        
        try:
            async for event in call_llm_stream_openai(
                messages=messages, 
                tools=tools_schema if tools_schema else None, 
                http_client=http_client, 
                tracing_data={"tracer": tracer, "trace_id": trace_id, "parent_span_id": parent_span_id}
                ):
                
                event_type = event["type"]
                data = event["data"]
                
                # ─────────────────────────────────────────────
                # TOKEN STREAM
                # ─────────────────────────────────────────────
                if event_type == "token":

                    # You can work with allucination mesuare here

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
                            buffer = ""
                # ─────────────────────────────────────────────
                # TOOL CALL REDUCTION
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
                # FINISH HANDLING
                # ─────────────────────────────────────────────
                elif event_type == "finish":

                    reason = data

                    logger.info(
                        f"[llm_stream] finish reason: {reason}"
                    )

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

                        # Always put a LIST, never a bare ToolCallChunk
                        assert isinstance(mapped_calls, list) 
                        sentence_queue.put_nowait(mapped_calls)

                        tool_calls_acc.clear()
                
            if buffer.strip():
                    logger.info(f"[llm_stream] Flushing remainder: '{buffer.strip()}'")
                    sentence_queue.put_nowait(buffer.strip())

        except CancelledError as cancel_error:
            logger.info("[llm_stream] Cancelled by signal.")
            sentence_queue.put_nowait(cancel_error)
        except Exception as e:
            logger.error(f"[llm_stream] Unexpected error in worker: {e}")
            sentence_queue.put_nowait(e)
        finally:
            sentence_queue.put_nowait(None) # EOF
    '''
    try:
        async for item in source:

            if isinstance(item, WarmUp):
                logger.info("LLM: WarmUp received. Generating initial greeting...")
                # Option A: A hardcoded greeting (fastest)
                yield item

            if isinstance(item, EndOfStream):
                yield item
                break
            if isinstance(item, AskUserStillThere) and current_task and current_task.done():
                yield item
            
            if isinstance(item, StartSpeaking):
                logger.info(" [INTERRUPT] llm_stream received StartSpeaking. Cancelling current LLM task.")
                if current_task:
                    current_task.cancel()
                    await asyncio.gather(current_task, return_exceptions=True)
                    current_task = None
                    sentence_queue = None  # ← orphan it, GC handles cleanup, no drain needed
                yield item


            if isinstance(item, str):
                logger.info(f"[llm_stream] Received string Question: {repr(item)}")
                timeout_curr = 2.0
                sentence_queue = asyncio.Queue()  # ← fresh queue per request, not shared state
                full_response = ""
                tool_calls = []
                
                # The extra function to call for a llm provider bring flexibility at runtime
                # It allows to add new providers it one is down 
                current_task = asyncio.create_task(get_llm_provider_worker(provider_name, model)(
                                            text=item,
                                            memory=message_history, 
                                            tools=tools, 
                                            http_client=http_client,
                                            tracing_data={"tracer": tracer, "trace_id": trace_id, "parent_span_id": parent_span_id},
                                            model=model,
                                            system_prompt=system_prompt,
                                            provider_url=provider_url,
                                            api_key=api_key,
                                            llm_config=llm_config,
                                            queue=sentence_queue,
                                            ))
                
                # Consume from queue until None or Interrupted
                while True:
                    try:
                        # 1. WAIT WITH TIMEOUT
                        # If LLM doesn't yield a sentence in 5s, send a placeholder
                        item_from_queue = await asyncio.wait_for(
                            sentence_queue.get(), 
                            timeout=timeout_curr
                        )


                        if isinstance(item_from_queue, list): # List of ToolCallChunk and results
                            
                            for tool_chunk, result in item_from_queue:
                                logger.info(f"[llm_stream] Tool call result: {tool_chunk.name} -> {result}")

                            # Call LLM again with tool results included
                            if current_task and not current_task.done():
                                current_task.cancel()
                                await asyncio.gather(current_task, return_exceptions=True)
                                
                                # I think is not need to clean the queue here, should be clean
                                logger.info(f"[llm_stream_sentence_queue] is empty: {sentence_queue.empty()}")
                            
                            # The extra function to call for a llm provider bring flexibility at runtime
                            # It allows to add new providers it one is down or not available  
                            current_task = asyncio.create_task(get_llm_provider_worker(provider_name, model)(
                                            text=item,
                                            memory=message_history, 
                                            tools=tools, 
                                            http_client=http_client,
                                            tracing_data={"tracer": tracer, "trace_id": trace_id, "parent_span_id": parent_span_id},
                                            model=model,
                                            system_prompt=system_prompt,
                                            provider_url=provider_url,
                                            api_key=api_key,
                                            llm_config=llm_config,
                                            queue=sentence_queue,
                                            ))
                            continue
                    

                        # 2. HANDLE RESULTS
                        if item_from_queue is None: # Normal finish
                            await message_history.add_user_message(item)
                            await message_history.add_ai_message(full_response, None) 
                            break
                        
                        if isinstance(item_from_queue, EndOfStream):
                            logger.info("[llm_stream] EndOfStream from LLM worker. Finishing conversation.")
                            # Good bye message
                            yield "Hasta luego"
                            await message_history.add_user_message(item)
                            await message_history.add_ai_message("Hasta luego", None)
                            await asyncio.sleep(0.8) # Wait for the TTS to finish and send EndOfStream
                            yield EndOfStream()
                            break
                            

                        if isinstance(item_from_queue, Exception):
                            logger.info(f"LLM: Exception in worker: {item_from_queue}")
                            yield "Lo siento, tuve un problema técnico al procesar su pregunta."
                            break
                        
                        if isinstance(item_from_queue, asyncio.CancelledError):
                            logger.info("LLM: Loop cancelled during barge-in.")
                            # Remove Last user question without reply to prevent history pollution
                            if message_history.in_memory and message_history.in_memory[-1]['role'] == 'user':
                                await message_history.rewind_last_message_x(1)
                            break

                        
                        full_response += item_from_queue
                        yield item_from_queue # It's a real sentence

                    except asyncio.TimeoutError:
                        
                        if timeout_curr < timeout_limit:
                            logger.warning("LLM: Producer slow Thinking...")
                            yield "Estoy pensando"
                            timeout_curr += 2.0
                            # Maybe is good to try another provider or default beside current configured.
                            # It will add more robustness to the system.
                            # Refactor: Add a fallback provider mechanism below.
                            continue
                        else:
                            logger.error("LLM: Producer cancelled. Timeout.")
                            yield "Lo siento, tuve un problema técnico al procesar su pregunta."
                            break

                    except asyncio.CancelledError:
                        logger.info("LLM: Loop cancelled during barge-in.")
                        raise
                    
                    except Exception as e:
                        logger.error(f"LLM: Unexpected error in consumer: {e}")
                        yield "Hubo un error inesperado."
                        break
    except asyncio.CancelledError:
        logger.info("LLM: Producer cancelled.")
        raise  # let it propagate cleanly
    except Exception as e:
        logger.error(f"LLM: Unexpected error in producer: {type(e).__name__}: {e!r}", exc_info=1)
        # or even better, get the traceback:
        logger.exception("LLM: Unexpected error in producer")
        yield "Hubo un error inesperado."
    finally:
        # Cleanup task if it was orphaned by an error or cancellation
        if current_task and not current_task.done():
            current_task.cancel()
            await asyncio.gather(current_task, return_exceptions=True)
        #if sentence_queue:
        #    sentence_queue.put_nowait(None) # EOF