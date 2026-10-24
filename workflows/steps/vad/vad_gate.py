
from workflows.steps.turn.utils import predict_endpoint
from workflows.signals import SignalFrame, WarmUp, AskUserStillThere, EndOfStream, StartSpeaking, EndSpeaking
from .utils import silero_has_speech_from_numpy, silero_reset_states
from yaafpy.types import ExecContext
from av import AudioFrame
from typing import AsyncGenerator
from enum import Enum
from collections import deque
import logging, uuid, time, asyncio
import numpy as np

from .workers import vad_backpressure_input

from opentelemetry.trace import Status, StatusCode, set_span_in_context
from opentelemetry import baggage

from concurrent.futures import ThreadPoolExecutor


from .config import (
    CONFIRM_TURN, MAX_UTTERANCE_LEN,
    INACTIVITY_STOP, MAX_ASK_USER, INACTIVITY_FRAMES,
    START_FRAMES, STOP_FRAMES, VAD_CHUNK_SAMPLES
)

logger = logging.getLogger(__name__)



# ════════════════════════════════════════════════════════════════════════════════
# Stage 1  –  VAD gate
# AudioFrame  →  list[AudioFrame]  (one complete utterance)
# Frames with a sample rate of 16 KHz. Mono. Ready to sed to silero vad
# ════════════════════════════════════════════════════════════════════════════════

class VADState(Enum):
    QUIET    = 1
    STARTING = 2
    SPEAKING = 3
    STOPPING = 4

async def vad_gate(source: AsyncGenerator, ctx: ExecContext) -> AsyncGenerator[list[AudioFrame], None]:

    conversation_span = ctx.shared_data["trace_context"]["conversation_span"]
    tracer = ctx.shared_data["resources"]["tracer"]
    session_id = str(ctx.shared_data["session_id"])

    state          = VADState.QUIET
    starting_count = 0
    stopping_count = 0
    
    '''
        The actual confirmed user speech
    '''
    utterance_buf: list[np.ndarray] = [] # grows unbounded during speech — no deque cap
    buffer = np.empty(0, dtype=np.float32) # accumulates SILERO_ACCUM frames before VAD call
        
    consecutive_silence_counter = 0
    ask_user = 0
    turn_count = 0
    end_speech_check = False
    current_turn = None

    vad_queue: asyncio.Queue[np.ndarray] = asyncio.Queue()
    
    n_chunks = 0

    

    # Consumer
    try:

        yield WarmUp()  # agent greets user immediately

        harvester_task = asyncio.create_task(vad_backpressure_input(source, vad_queue))


        _executor = ThreadPoolExecutor(max_workers=1)
        while True:

            pcm = await vad_queue.get()
            logger.debug("VAD: dequeued item")

            if pcm is None:  # shutdown signal
                logger.info("VAD: received shutdown sentinel")
                break

            # Normalize incoming audio to mono 1D float32 samples
            pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)  

            buffer = np.concatenate((buffer, pcm))

            logger.debug(
                "VAD: input=%d samples, buffer=%d, chunk_size=%d",
                len(pcm),
                len(buffer),
                VAD_CHUNK_SAMPLES,
            )

            while len(buffer) >= VAD_CHUNK_SAMPLES:
                chunk = buffer[:VAD_CHUNK_SAMPLES]
                buffer = buffer[VAD_CHUNK_SAMPLES:]

                try:
                    confident = silero_has_speech_from_numpy(chunk)
                    #logger.info(f"VAD: confident={confident}")
                except Exception:
                    logger.exception("VAD: inference failed on this chunk")
                    raise
                

                n_chunks += 1
                if n_chunks % 25 == 0:
                    logger.info(
                        f"VAD dbg | state={state.name} conf={confident} "
                        f"peak={np.abs(chunk).max():.3f} buf={len(utterance_buf)} "
                        f"stop={stopping_count} q={vad_queue.qsize()} ask={ask_user}"
                    )

                try:
                    if confident:
                        match state:
                            case VADState.QUIET:
                                state          = VADState.STARTING
                                starting_count = 1

                                # Recover the previous frame and include the current frame.
                                utterance_buf.append(chunk)
                                logger.debug(f"VAD: Start detected.")

                            case VADState.STARTING:
                                utterance_buf.append(chunk)
                                starting_count += 1
                                if starting_count >= START_FRAMES:
                                    state = VADState.SPEAKING
                                    consecutive_silence_counter = 0
                                    ask_user = 0
                                    
                                    # SIGNAL 1: Signal other process to Stop the workers Barg-IN
                                    yield StartSpeaking()
                                    
                            case VADState.SPEAKING:
                                utterance_buf.append(chunk)
                            case VADState.STOPPING:
                                utterance_buf.append(chunk)
                                state          = VADState.SPEAKING
                                stopping_count = 0
                    else:
                        match state:
                            case VADState.QUIET: # The Inactivity Period or Silences Just Extends
                                consecutive_silence_counter += 1
                                # Check for hard stop (e.g., 120 seconds)
                                if consecutive_silence_counter >= INACTIVITY_STOP:
                                    logger.info("VAD: Absolute inactivity limit reached. Closing pipeline.")
                                    yield EndOfStream()
                                    return # This kills the generator
                                if ask_user < MAX_ASK_USER and consecutive_silence_counter >= INACTIVITY_FRAMES:
                                    logger.info("VAD: Max silence between utterances reached. Reactivating.")
                                    yield AskUserStillThere()
                                    ask_user += 1
                            case VADState.STARTING: # Just Noise or False or Insuficiente Speech Detection
                                state          = VADState.QUIET
                                starting_count = 0
                                utterance_buf  = []
                            case VADState.SPEAKING: # Could be a pause  or posible start of the end of speech
                                utterance_buf.append(chunk)
                                state          = VADState.STOPPING
                                stopping_count += 1
                            case VADState.STOPPING: # Extend the stopping Pause
                                stopping_count += 1    
                                try:
                                    # Utterance Buf should exist because Stopping came after  speaking 
                                    if utterance_buf and stopping_count >= CONFIRM_TURN:
                                        audio = np.concatenate(utterance_buf).astype(np.float32, copy=False)
                                        prediction = predict_endpoint(audio)
                                        probability = prediction.get("probability", 0.0) if isinstance(prediction, dict) else float(prediction)

                                        if probability >= 0.60:
                                            logger.info(f"TURN: Endpoint detected (prob: {probability:.2f}). Ending Speaking Turn Start.")
                                            state          = VADState.QUIET
                                            # SIGNAL 3: User is done speaking by model prediction.

                                            # SIGNAL 0: Signal new Turn start and stop old turn if there is one
                                            if current_turn:
                                                current_turn.set_status(Status(StatusCode.OK))
                                                current_turn.end()
                                            
                                            turn_count+=1
                                            
                                            turn_context = set_span_in_context(conversation_span)

                                            turn_context = baggage.set_baggage(
                                                "turn_sequence",
                                                turn_count,
                                                context=turn_context,
                                            )
                                            
                                            current_turn = tracer.start_span(
                                                            f"turn.{turn_count}",
                                                            context=turn_context,
                                                            attributes={
                                                                "session.id": session_id,
                                                                "turn.id": str(uuid.uuid4()),
                                                                "turn.seq": turn_count,
                                                            },
                                                        )
                                            yield current_turn


                                            yield EndSpeaking()
                                            # 4. NOW send the data
                                            yield utterance_buf
                                            utterance_buf  = []
                                            starting_count = 0
                                            stopping_count = 0
                                            consecutive_silence_counter = 0
                                            silero_reset_states()
                                            continue # Skip the hard STOP_FRAMES check if endpointing triggers successfully
                                except Exception:
                                    logger.exception("TURN: inference failed during stopping state")      

                                if stopping_count >= STOP_FRAMES:
                                    
                                    state          = VADState.QUIET
                                    # SIGNAL 3: User is done speaking by silence time.

                                    # SIGNAL 0: Signal new Turn start and stop old turn if there is one
                                    if current_turn:
                                        current_turn.set_status(Status(StatusCode.OK))
                                        current_turn.end()
                                    
                                    turn_count+=1
                                    
                                    turn_context = set_span_in_context(conversation_span)

                                    turn_context = baggage.set_baggage(
                                        "turn_sequence",
                                        turn_count,
                                        context=turn_context,
                                    )
                                    
                                    current_turn = tracer.start_span(
                                                    f"turn.{turn_count}",
                                                    context=turn_context,
                                                    attributes={
                                                        "session.id": session_id,
                                                        "turn.id": str(uuid.uuid4()),
                                                        "turn.seq": turn_count,
                                                    },
                                                )
                                    yield current_turn

                                    yield EndSpeaking()
                                    # 4. NOW send the data
                                    yield utterance_buf
                                    utterance_buf  = []
                                    starting_count = 0
                                    stopping_count = 0
                                    consecutive_silence_counter = 0
                except (GeneratorExit, asyncio.CancelledError):
                    raise                    
                except:
                    # Defensive: never let a state-machine bug kill the whole pipeline.
                    # Reset to a known-safe state and keep the stream alive.
                    logger.exception("VAD: state machine error, resetting to QUIET", exc_info=1)
                    state          = VADState.QUIET
                    starting_count = 0
                    stopping_count = 0
                    utterance_buf  = []
                    consecutive_silence_counter = 0

    except asyncio.CancelledError:
        logger.exception("Cancellation Error", exc_info=1)
        pass

    except Exception:
        # Defensive: catch anything from the upstream `source` itself
        # (disconnects, decode errors, etc.) so we exit cleanly instead
        # of raising an unhandled exception out of the generator.
        logger.exception("VAD: fatal error reading from source, ending stream", exc_info=1)
        # Close current turn
        if current_turn:
            current_turn.set_status(Status(StatusCode.ERROR))
            current_turn.end()
            current_turn = None
        yield EndOfStream()
        return

    finally: 
        _executor.shutdown(wait=False)

        harvester_task.cancel()
        if current_turn:
            current_turn.set_status(Status(StatusCode.OK))
            current_turn.end()