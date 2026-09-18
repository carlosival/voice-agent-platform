from .config import (
    PRE_ROLL_LEN, MAX_UTTERANCE_LEN, SILERO_ACCUM,
    INACTIVITY_STOP, MAX_ASK_USER, INACTIVITY_FRAMES,
    START_FRAMES, STOP_FRAMES, 
)
from workflows.signals import SignalFrame, WarmUp, AskUserStillThere, EndOfStream, StartSpeaking, EndSpeaking
from .utils import silero_has_speech_from_numpy
from yaafpy.types import ExecContext
from av import AudioFrame
from typing import AsyncGenerator
from enum import Enum
from collections import deque
import logging
import numpy as np


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
    state          = VADState.QUIET
    starting_count = 0
    stopping_count = 0
    utterance_buf  = []      # grows unbounded during speech — no deque cap
    silero_buf     = []      # accumulates SILERO_ACCUM frames before VAD call
    consecutive_silence_counter = 0
    ask_user = 0
    
    # This holds audio history during silent periods
    pre_roll_history = deque(maxlen=PRE_ROLL_LEN)


    yield WarmUp()  # agent greets user immediately


    try:
        frame_count = 0
        async for frame in source:
            frame_count += 1
            if frame_count % 100 == 0:
                logger.info(
                        f"Frame #{frame_count} | "
                        f"format={frame.format.name} | "
                        f"layout={frame.layout.name} | "
                        f"channels (metadata): {frame.layout.nb_channels} | "
                        f"is_planar={frame.format.is_planar} | "
                        f"sample_rate={frame.sample_rate} | "
                        f"samples={frame.samples} | "
                )

            # ---- Signal control frames ----
            if isinstance(frame, SignalFrame):
                if isinstance(frame, WarmUp):
                    yield WarmUp()
                elif isinstance(frame, AskUserStillThere):
                    yield AskUserStillThere()
                else:
                    # Defensive: unknown signal type — log and drop it,
                    # don't let it fall through into audio processing below.
                    logger.warning(f"VAD: unhandled SignalFrame subtype {type(frame)!r}, ignoring")
                continue
            
            # ---- Defensive: make sure it's actually an audio frame ----
            if not isinstance(frame, AudioFrame):
                logger.warning(f"VAD: expected AudioFrame, got {type(frame)!r}, skipping")
                continue

            silero_buf.append(frame)

            pre_roll_history.append(frame) # Always track history

            if state in (VADState.STARTING, VADState.SPEAKING, VADState.STOPPING):
                utterance_buf.append(frame)

            if len(silero_buf) < SILERO_ACCUM:
                continue

            # Save before reset so QUIET→STARTING can backfill
            evaluated_frames = list(silero_buf)
            silero_buf = []
            
            # ---- Defensive: isolate VAD inference from the state machine ----
            try:
                pcm = np.concatenate([f.to_ndarray().reshape(-1) for f in evaluated_frames])
                confident = silero_has_speech_from_numpy(pcm)
                #logger.info(f"VAD: confident={confident}")
            except Exception:
                logger.exception("VAD: inference failed on this chunk")
                raise
            
            try:
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
                                yield EndOfStream()
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
            except:
                # Defensive: never let a state-machine bug kill the whole pipeline.
                # Reset to a known-safe state and keep the stream alive.
                logger.exception("VAD: state machine error, resetting to QUIET", exc_info=1)
                state          = VADState.QUIET
                starting_count = 0
                stopping_count = 0
                utterance_buf  = []
                consecutive_silence_counter = 0


    except Exception:
        # Defensive: catch anything from the upstream `source` itself
        # (disconnects, decode errors, etc.) so we exit cleanly instead
        # of raising an unhandled exception out of the generator.
        logger.exception("VAD: fatal error reading from source, ending stream", exc_info=1)
        yield EndOfStream()
        return