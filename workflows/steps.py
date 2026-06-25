from yaafpy.types import ExecContext, WorkflowAbortException
from yaafpy import StreamWorkflow
from av import AudioFrame
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from typing import AsyncGenerator
from asyncio import gather, Queue, Event, QueueEmpty, ensure_future, CancelledError, create_task, wait_for, sleep, timeout, TimeoutError, CancelledError
from collections import deque
from workflows.audio_track import AudioOutputTrack
from workflows.signals import StartSpeaking, EndSpeaking, EndOfStream, AskUserStillThere, WarmUp, SignalFrame
import logging
import wave, uuid, os 
import numpy as np
import json
from enum import Enum
import httpx
from workflows.utils.memory import InMemoryMemory
from workflows.utils.context import build_chat_messages

from smolagents.models import get_tool_json_schema
from smolagents import Tool
from workflows.utils.tools import ToolCallChunk, execute_tool

from workflows.utils import (
    layered_has_speech,
    call_stt_from_frames_openai,
    call_stt_from_frames_speaches,
    call_llm_stream_openai,
    call_tts_stream,
    pcm_to_wav,
    frames_to_pcm,
    silero_has_speech_from_numpy,
    frames_to_mono_int16
)


logger = logging.getLogger(__name__)

class VADState(Enum):
    QUIET    = 1
    STARTING = 2
    SPEAKING = 3
    STOPPING = 4


# Tunable thresholds in real time
START_SECS   = 0.2   # confirmed speech after this long
STOP_SECS    = 0.6   # confirmed silence after this long
FRAME_SECS   = 0.02  # 20ms per aiortc frame
INACTIVITY_SECS = 60 # 60 seconds At this point the agent will ask the user still there?

START_FRAMES = round(START_SECS / FRAME_SECS)   # 10 frames
STOP_FRAMES  = round(STOP_SECS  / FRAME_SECS)   # 40 frames
INACTIVITY_FRAMES = round(INACTIVITY_SECS / FRAME_SECS) # 60 seconds At this point the agent will ask the user still there?
INACTIVITY_STOP = 2 * INACTIVITY_FRAMES
MAX_ASK_USER = 1
SILERO_ACCUM = 3                                 # frames to accumulate before Silero call
                                                 # 3 × 320 = 960 samples > 512 min ✓

SAMPLE_RATE     = 16_000
VAD_WINDOW_SIZE      = 50          # frames per VAD call  (~1 s @ 20 ms/frame)
VAD_SILENCE_WINDOWS_LIMIT = 3      # silent windows after speech → flush utterance
VAD_THRESHOLD   = 0.5
VAD_STRIDE    = 10  # call VAD every 20 frames -> call every 400ms
maxsize = 50

# ──────────────────────────────────────────────
# Stage 0 — async generator wrapping the track
# ──────────────────────────────────────────────
async def track_frames(track: MediaStreamTrack) -> AsyncGenerator[AudioFrame, None]:
    frame_count = 0
    wait_for_timeout = 120
    try:
        while True:
            try:
                frame = await wait_for(track.recv(), timeout=wait_for_timeout)
                frame_count += 1

                if frame_count <= 3:  # inspect first 3 frames only
                    arr = frame.to_ndarray()
                    logger.info(
                        f"Frame #{frame_count} | "
                        f"format={frame.format.name} | "
                        f"layout={frame.layout.name} | "
                    f"sample_rate={frame.sample_rate} | "
                    f"samples={frame.samples} | "
                    f"shape={arr.shape} | "
                    f"dtype={arr.dtype} | "
                    f"min={arr.min()} max={arr.max()} "
                    f"rms={np.sqrt(np.mean(arr.astype(np.float32)**2)):.1f}"
                )

                if frame_count % 100 == 0:
                    logger.info(f"Track received frame {frame_count}")
                yield frame
            except TimeoutError:
                logger.info("Track: No audio for 120 seconds, connection is likely dead")
                break    
            except CancelledError:
                logger.info("Track: Cancelled.")
                break
            except (MediaStreamError, TimeoutError):
                logger.info("Track: Connection lost")
                break
    finally:
        track.stop() 
        logger.info("Closing MediaStreamTrack") 
          
        
# ════════════════════════════════════════════════════════════════════════════════
# Stage 1  –  VAD gate
# AudioFrame  →  list[AudioFrame]  (one complete utterance)
# ════════════════════════════════════════════════════════════════════════════════

async def vad_gate(source: AsyncGenerator, ctx: ExecContext) -> AsyncGenerator[list[AudioFrame], None]:
    PRE_ROLL_LEN = 50
    MAX_UTTERANCE_LEN = 1000 # 1000frames * 20ms = 20min
    #speaking_event = ctx.shared_data["events"]["speaking_event"]
    output_track: AudioOutputTrack = ctx.shared_data["resources"]["output_track"]
    state          = VADState.QUIET
    starting_count = 0
    stopping_count = 0
    utterance_buf  = []      # grows unbounded during speech — no deque cap
    silero_buf     = []      # accumulates SILERO_ACCUM frames before VAD call
    consecutive_silence_counter = 0
    ask_user = 0
    # NEW: This holds audio history during silent periods
    pre_roll_history = deque(maxlen=PRE_ROLL_LEN)


    #yield WarmUp()  # agent greets user immediately

    async for frame in source:

        # Signal control frames
        if isinstance(frame, SignalFrame):
            if isinstance(frame, WarmUp):
                yield WarmUp()
                continue
            elif isinstance(frame, AskUserStillThere):
                yield AskUserStillThere()
                continue

        silero_buf.append(frame)

        pre_roll_history.append(frame) # Always track history

        if state in (VADState.STARTING, VADState.SPEAKING, VADState.STOPPING):
            utterance_buf.append(frame)

        if len(silero_buf) < SILERO_ACCUM:
            continue

        # Save before reset so QUIET→STARTING can backfill
        evaluated_frames = list(silero_buf)
        pcm              = frames_to_mono_int16(evaluated_frames)
        confident        = silero_has_speech_from_numpy(pcm)
        silero_buf       = []

        if confident:
            match state:
                case VADState.QUIET:
                    state          = VADState.STARTING
                    starting_count = SILERO_ACCUM

                    # Instead of just starting fresh, we take the history
                    # This ensures Whisper hears the "H" in "Hello"
                    utterance_buf = list(pre_roll_history)
                    logger.debug(f"VAD: Start detected. Pre-roll added {PRE_ROLL_LEN} frames.")

                case VADState.STARTING:
                    starting_count += SILERO_ACCUM
                    if starting_count >= START_FRAMES:
                        logger.info(f"VAD: SPEAKING ({starting_count*20}ms of speech)")
                        state = VADState.SPEAKING
                        consecutive_silence_counter = 0
                        ask_user = 0
                        # SIGNAL 1: Tell everyone to SHUT UP right now
                        yield StartSpeaking()
                case VADState.SPEAKING:
                    pass
                case VADState.STOPPING:
                    state          = VADState.SPEAKING
                    stopping_count = 0
        else:
            match state:
                case VADState.QUIET:
                    consecutive_silence_counter += SILERO_ACCUM
                    # Check for hard stop (e.g., 120 seconds)
                    if consecutive_silence_counter >= INACTIVITY_STOP:
                        logger.info("VAD: Absolute inactivity limit reached. Closing pipeline.")
                        yield EndOfStream
                        return # This kills the generator
                    if ask_user < MAX_ASK_USER and consecutive_silence_counter >= INACTIVITY_FRAMES:
                        logger.info("VAD: Max silence between utterances reached. Reactivating.")
                        yield AskUserStillThere()
                        ask_user += 1
                case VADState.STARTING:
                    state          = VADState.QUIET
                    starting_count = 0
                    utterance_buf  = []
                case VADState.SPEAKING:
                    state          = VADState.STOPPING
                    stopping_count = SILERO_ACCUM
                case VADState.STOPPING:
                    stopping_count += SILERO_ACCUM
                    if stopping_count >= STOP_FRAMES:
                        logger.info(f"VAD: → {len(utterance_buf)} frames ({len(utterance_buf)*20}ms)")
                        state          = VADState.QUIET
                        # SIGNAL 3: User is done.
                        yield EndSpeaking()
                        # 4. NOW send the data
                        yield utterance_buf
                        utterance_buf  = []
                        starting_count = 0
                        stopping_count = 0
                        consecutive_silence_counter = 0

# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 1.5  –  VAD Decoupler
# list[AudioFrame]  →  list[AudioFrame]
# ════════════════════════════════════════════════════════════════════════════════

async def brain_bridge(source: AsyncGenerator, ctx: ExecContext) -> AsyncGenerator:
    
    queue = Queue(maxsize=10)
    
    # Track if we are already shutting down to avoid double-closing
    is_closing = False

    async def input_harvester():
        nonlocal is_closing
        try:
            async for item in source:
                if isinstance(item, WarmUp):
                    logger.info("Bridge: Warmup signal received.")
                if isinstance(item, AskUserStillThere):
                    logger.info("Bridge: User still there signal received.")
                    #utterance queue should be empty
                    if queue.empty():
                        logger.info("Bridge: Utterance queue is empty.")
                    else:
                        logger.warning("Bridge: Utterance queue is not empty.")
                if isinstance(item, StartSpeaking):
                    # Clear the queue so Turn 2 doesn't sit behind Turn 1's leftovers
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                        except QueueEmpty:
                            break
                    logger.info("Bridge: Purged track and queue on StartSpeaking")
                
                if isinstance(item, EndOfStream):
                    logger.info("Bridge: Received EndOfStream, shutting down.")
                    await wait_for(queue.put(item), timeout=2.0)
                    return # Exit the harvester loop
                try:
                    await wait_for(queue.put(item), timeout=2.0)
                except TimeoutError:
                    logger.warning("Bridge: Queue full and blocked for too long. Dropping frame.")  
        except (CancelledError, GeneratorExit, MediaStreamError):
            is_closing = True
        except Exception as e:
            logger.error(f"Bridge Harvester Error: {e}")
        finally:
            try:
                queue.put_nowait(None)
            except QueueFull:
                try:
                    queue.get_nowait() 
                    queue.put_nowait(None)
                except Exception:
                    logger.error("Bridge: Failed to send None sentinel to queue.")

    harvester_task = create_task(input_harvester())

    try:
        while True:
            item = await queue.get()
            if isinstance(item, EndOfStream):
                yield item
                break
            if item is None:
                break
            yield item
    finally:
        # CLEANUP WITHOUT THE RUNTIME ERROR:
        # We check if the harvester is already done before trying to kill it
        if not harvester_task.done():
            harvester_task.cancel()
            try:
                # Shield the cleanup so it doesn't conflict with yaafpy's internal aclose()
                await harvester_task 
            except CancelledError:
                pass



# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 2  –  STT
# list[AudioFrame]  →  str
# ════════════════════════════════════════════════════════════════════════════════

async def stt(
    source: AsyncGenerator,
    ctx:    ExecContext,
) -> AsyncGenerator:
    """
    Receives utterances, handles interruptions via task cancellation,
    and logs raw PCM to disk for debugging.
    """

    current_task = None
    http_client: httpx.AsyncClient = ctx.shared_data["resources"]["http_client"]

    async for item in source:

        if isinstance(item, EndOfStream):
            if current_task and not current_task.done():
                current_task.cancel()
            yield item
            break

        # If we get a AskUserStillThere signal and task is done, yield it
        if isinstance(item, AskUserStillThere) and current_task and current_task.done():
            yield item
   
        # If we get a Stop signal, kill the Whisper task immediately
        if isinstance(item, StartSpeaking):
            if current_task and not current_task.done():
                current_task.cancel()
            yield item

        if isinstance(item, WarmUp):
            # We know audio is coming, but we ignore until UserFinished
            yield item

        if isinstance(item, list): # This is the actual AudioBuffer
            
            debug = True
            
            if debug:
                # Setup Debug Directory
                debug_dir = "./static/stt_debug"
                os.makedirs(debug_dir, exist_ok=True)

                # At this point, item is list[AudioFrame]
                frames = item
                pcm = frames_to_mono_int16(frames)

                # 2. DEBUG LOGGING: Save the exact PCM sent to Whisper
                # We do this before the task so we have the file even if the task is cancelled
                filename = os.path.join(debug_dir, f"{uuid.uuid4().hex}_{len(frames)}frames.wav")
                try:
                    with wave.open(filename, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(48000)   # Faster-Whisper standard
                        wf.writeframes(pcm.tobytes())
                    logger.info(f"STT debug saved: {filename} | samples={len(pcm)} | rms={np.sqrt(np.mean(pcm.astype(np.float32)**2)):.1f}")
                except Exception as e:
                    logger.error(f"STT Debug Save Failed: {e}")

            # Launch Whisper as a task so we can cancel it if a StopAndClear arrives later
            current_task = create_task(call_stt_from_frames_openai(http_client, item))
            
            try:
                transcript = await current_task
                if transcript: yield transcript
            except CancelledError:
                logger.info("STT: Task killed by StartSpeaking signal.")
            finally:
                # Cleanup task if it was orphaned by an error or cancellation
                if not current_task.done():
                    current_task.cancel()


# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 3  –  LLM streaming
# str  →  str  (sentence-level chunks)
# ════════════════════════════════════════════════════════════════════════════════

SENTENCE_ENDS = {",", ".",  "\n", "\r", "\n\n", "\r\n", "!", "?", "…", "。"}

async def llm_stream(
    source: AsyncGenerator,
    ctx:    ExecContext,
) -> AsyncGenerator:
    """
    Streams LLM token-by-token via call_llm_stream.
    Flushes to downstream on sentence boundaries so TTS starts early.
    Only saves complete, uninterrupted responses to message_history.
    Interrupted responses are logged and discarded to keep history clean.
    """

    current_task = None
    sentence_queue = None  # ← initialize to None, not unbound
    http_client:     httpx.AsyncClient = ctx.shared_data["resources"]["http_client"]
    message_history: InMemoryMemory = ctx.shared_data["message_history"]
    tools: Dict[str, Tool] = ctx.shared_data.get("tools", {}) # Dict[str, Tool] smolagent
    tracer = ctx.shared_data["resources"]["tracer"]
    trace_id = ctx.shared_data["trace_context"]["trace_id"]
    parent_span_id = ctx.shared_data["trace_context"]["parent_span_id"]
    timeout_limit = 5.0
    
    async def llm_stream_worker(text, sentence_queue):
        token_count = 0
        buffer = ""
        messages = build_chat_messages(await message_history.get_messages())
        tools_schema = [get_tool_json_schema(t) for t in tools.values()]
        tool_calls_acc: dict[int, dict] = {}
        try:
            async for event in call_llm_stream_openai(messages=messages, tools=tools_schema, http_client=http_client, tracing_data={"tracer": tracer, "trace_id": trace_id, "parent_span_id": parent_span_id}):
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
                            sentence_queue.put_nowait(buffer.strip())
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
                    await gather(current_task, return_exceptions=True)
                    current_task = None
                    sentence_queue = None  # ← orphan it, GC handles cleanup, no drain needed
                yield item


            if isinstance(item, str):
                logger.info(f"[llm_stream] Received string Question: {repr(item)}")
                await message_history.add_user_message(item)
                msg = await  message_history.get_messages()
                logger.info(f"[message_history] {msg}")
                timeout_curr = 2.0
                sentence_queue = Queue()  # ← fresh queue per request, not shared state
                full_response = ""
                tool_calls = []
                current_task = create_task(llm_stream_worker(item, sentence_queue))
                # Consume from queue until None or Interrupted
                while True:
                    try:
                        # 1. WAIT WITH TIMEOUT
                        # If LLM doesn't yield a sentence in 5s, send a placeholder
                        item_from_queue = await wait_for(
                            sentence_queue.get(), 
                            timeout=timeout_curr
                        )


                        if isinstance(item_from_queue, list): # List of ToolCallChunk
                            
                            # Could be sequential or parallel, as needed
                            # Parallel execution via asyncio.gather if async tools
                            results = await gather(*[execute_tool(tc, tools) for tc in item_from_queue])
                            logger.info(f"[llm_stream] Tool call results: {results}")
                            
                            if EndOfStream in [type(r.get("result", None)) for r in results]:
                                logger.info("[llm_stream] EndOfStream from tool result. Finishing conversation.")
                                await message_history.add_ai_message("¡Hasta Luego!", None)
                                yield EndOfStream()
                                break

                            # Add tools calls and results to message history
                            await message_history.add_tools_calls(item_from_queue)
                            await message_history.add_tools_results(results)
                            # Call LLM again with tool results
                            if current_task and not current_task.done():
                                current_task.cancel()
                                await gather(current_task, return_exceptions=True)
                                
                                # I think is not need to clean the queue here, should be clean
                                logger.info(f"[llm_stream_sentence_queue] is empty: {sentence_queue.empty()}")
                            
                            current_task = create_task(llm_stream_worker(item, sentence_queue))
                            continue
                    

                        # 2. HANDLE RESULTS
                        if item_from_queue is None: # Normal finish
                            await message_history.add_ai_message(full_response, None)
                            break
                        
                        if isinstance(item_from_queue, Exception):
                            logger.info(f"LLM: Exception in worker: {item_from_queue}")
                            yield "Lo siento, tuve un problema técnico al procesar su pregunta."
                            break
                        
                        if isinstance(item_from_queue, CancelledError):
                            logger.info("LLM: Loop cancelled during barge-in.")
                            # Remove Last user question without reply to prevent history pollution
                            if message_history.in_memory and message_history.in_memory[-1]['role'] == 'user':
                                await message_history.rewind_last_message_x(1)
                            break

                        
                        full_response += item_from_queue
                        yield item_from_queue # It's a real sentence

                    except TimeoutError:
                        
                        if timeout_curr < timeout_limit:
                            logger.warning("LLM: Producer slow Thinking...")
                            yield "Estoy pensando"
                            timeout_curr += 2.0
                            continue
                        else:
                            logger.error("LLM: Producer cancelled. Timeout.")
                            yield "Lo siento, tuve un problema técnico al procesar su pregunta."
                            break

                    except CancelledError:
                        logger.info("LLM: Loop cancelled during barge-in.")
                        raise
                    
                    except Exception as e:
                        logger.error(f"LLM: Unexpected error in consumer: {e}")
                        yield "Hubo un error inesperado."
                        break
    except CancelledError:
        logger.info("LLM: Producer cancelled.")
        raise  # let it propagate cleanly
    except Exception as e:
        logger.error(f"LLM: Unexpected error in producer: {str(e)}")
        yield "Hubo un error inesperado."
    finally:
        # Cleanup task if it was orphaned by an error or cancellation
        if current_task and not current_task.done():
            current_task.cancel()
            await gather(current_task, return_exceptions=True)
        #if sentence_queue:
        #    sentence_queue.put_nowait(None) # EOF


# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 4  –  TTS
# str  →  bytes  (WAV blob per sentence)
# ════════════════════════════════════════════════════════════════════════════════

async def tts(
    source: AsyncGenerator,
    ctx:    ExecContext,
) -> AsyncGenerator[bytes, None]:
    """
    Calls Speaches Kokoro-82M per sentence chunk via call_tts_stream.
    Forwards raw PCM byte chunks as they arrive for minimal latency.
    Skips synthesis entirely if speaking_event was set between LLM chunks.
    """

    http_client:    httpx.AsyncClient   = ctx.shared_data["resources"]["http_client"]
    output_track: AudioOutputTrack = ctx.shared_data["resources"]["output_track"]
    current_task = None
    tts_queue = Queue()
    
    # 3. DEFINE THE SYNTHESIS WORKER
    async def tts_worker(client: httpx.AsyncClient, track: AudioOutputTrack):
        while True:
            try:
                text = await tts_queue.get()
                if text is None:
                    break
                
                gen = call_tts_stream(http_client=http_client, text=text, debug=True)
                frame_count = 0
                
                async for pcm_chunk in gen:
                    # The code will only block here if 'call_tts_stream' hangs.
                    # As long as chunks are coming, this keeps running.
                    await track.push_pcm_bytes(pcm_chunk)
                    frame_count += 1
                
                if frame_count > 0:
                    await track.add_silence(duration_frames=20)
                    logger.info(f"TTS Worker: Finished {frame_count} frames for: {text}")
                
            except CancelledError:
                # Still vital for barge-in!
                logger.debug("TTS Worker: Cancelled (Barge-in).")
                raise 
            except Exception as e:
                logger.error(f"TTS Worker Error: {e}")

    
    # 1. Warm up the track for first time
    #await output_track.add_silence(duration_frames=15)

    current_task = create_task(tts_worker(http_client, output_track))

    try:
        async for sentence in source:
            if isinstance(sentence, WarmUp):
                logger.info(f"TTS received WarmUp signal.")
                await output_track.add_silence(duration_frames=50)
                await tts_queue.put("¡Hola! ¿Cómo puedo ayudarte?")

            if isinstance(sentence, EndOfStream):
                logger.info("TTS: EndOfStream received. Cleaning up.")
                # Optional: Send a goodbye message before killing
                await wait_for(tts_queue.put("¡Hasta luego!"), timeout=2.0) 
                await wait_for(tts_queue.put(None), timeout=2.0)
                break
            # 1. SIGNAL HANDLING (The "Kill Switch")
            if isinstance(sentence, StartSpeaking):
                logger.info("[AUDIO_KILL] TTS received StartSpeaking. Purging output track.")
                # Clear pending sentences
                while not tts_queue.empty():
                    tts_queue.get_nowait()
                # Cancel whatever is currently synthesizing
                if current_task and not current_task.done():
                    current_task.cancel()
                    try:
                        await current_task
                    except CancelledError:
                        pass
                output_track.purge()
                # Restart the sequential worker
                current_task = create_task(tts_worker(http_client, output_track))
                continue

            # 2. DATA HANDLING (The Sentence)
            if isinstance(sentence, str):
                logger.info(f"TTS received sentence: '{sentence}'")
                await wait_for(tts_queue.put(sentence), timeout=2.0)
            
            # 3. ASK USER STILL THERE
            if isinstance(sentence, AskUserStillThere):
                logger.info("TTS received AskUserStillThere signal.")
                if tts_queue.empty():
                    await wait_for(tts_queue.put("¿Te puedo ayudar en algo más?"), timeout=2.0)
            
    except Exception as e:
        logger.error(f"TTS Main Loop Exception: {e}")
        raise
    finally:
        await wait_for(tts_queue.put(None), timeout=2.0)  # shutdown sentinel
        if current_task and not current_task.done():
            current_task.cancel()
        # This is critical for yaafpy
        raise WorkflowAbortException("End of stream.")

    if False: yield  # ← makes Python treat this as an async generator function 

# ════════════════════════════════════════════════════════════════════════════════
# TRANSFORM 5  –  Frame sender  (sink)
# bytes  →  (side-effect: sends AudioFrames to output_track)
# ════════════════════════════════════════════════════════════════════════════════

async def frame_sender(
    source: AsyncGenerator,
    ctx:    ExecContext,
) -> AsyncGenerator:
    """
    Final Sink: Bridge between Step 4 (TTS) and WebRTC Output Track.
    Maintains a 160ms pre-roll buffer to eliminate inter-sentence jitter.
    """
    output_track: AudioOutputTrack = ctx.shared_data["resources"]["output_track"]
    speaking_event: Event          = ctx.shared_data["events"]["speaking_event"]
    SAMPLES      = 960          # 20ms at 48kHz

    async for pcm_bytes in source:
        if speaking_event.is_set():
            continue
        pcm_data = np.frombuffer(pcm_bytes, dtype=np.int16)
        for i in range(0, len(pcm_data), SAMPLES):
            chunk = pcm_data[i:i + SAMPLES]
            if len(chunk) < SAMPLES:
                chunk = np.pad(chunk, (0, SAMPLES - len(chunk)))
            await wait_for(output_track._queue.put(chunk), timeout=2.0)
        
    if False: yield  # ← makes Python treat this as an async generator function    


    