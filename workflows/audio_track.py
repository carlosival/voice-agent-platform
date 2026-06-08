import asyncio
import fractions
import time
import numpy as np
from av import AudioFrame
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError
import logging
logger = logging.getLogger(__name__)

SAMPLE_RATE      = 48000
CHANNELS         = 1
SAMPLES_PER_10MS = 480
BYTES_PER_10MS   = SAMPLES_PER_10MS * 2
SILENCE_10MS     = np.zeros(SAMPLES_PER_10MS, dtype=np.int16)



class AudioOutputTrack(MediaStreamTrack):
    """
    A WebRTC audio track that streams TTS audio back to the browser.
    Push PCM frames into it via push_audio() or push_silence().
    """
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue     = asyncio.Queue(maxsize=100)
        self._timestamp = 0
        self._start     = None
        self._ended     = False


    async def add_silence(self, duration_frames: int = 15):
        """
        Called at the start of every LLM/TTS response.
        Injects 150ms of silence to stabilize the WebRTC jitter buffer.
        """

        for _ in range(duration_frames):
            await self._queue.put(SILENCE_10MS.copy())
        logger.info(f"AudioOutputTrack: Warm-up silence injected for new turn: {duration_frames} frames.")

    async def recv(self) -> AudioFrame:
        if self._ended:
            raise MediaStreamError

        if self._start is None:
            self._start = time.monotonic()

        # Pacing: ensure we don't return frames faster than real-time
        # Use >= to catch the very first frame
        current_time = time.monotonic()
        expected_time = self._start + (self._timestamp / SAMPLE_RATE)
        
        if expected_time > current_time:
            await asyncio.sleep(expected_time - current_time)

        try:
            # Use a very short timeout or no timeout for the queue
            pcm = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pcm = SILENCE_10MS

        assert pcm.dtype == np.int16,         f"Wrong dtype:  {pcm.dtype}"
        assert len(pcm)  == SAMPLES_PER_10MS, f"Wrong length: {len(pcm)}"

        # 3. Correct Frame Construction
        frame = AudioFrame.from_ndarray(pcm[None, :], layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts         = self._timestamp
        
        # CRITICAL CHANGE: Use SAMPLE_RATE for the time_base
        frame.time_base   = fractions.Fraction(1, SAMPLE_RATE)
        
        self._timestamp  += SAMPLES_PER_10MS
        return frame
        
        

    async def push_audio(self, pcm: np.ndarray):
        """Push a numpy int16 array — chunks it into 10ms frames."""
        pcm = pcm.astype(np.int16)
        for i in range(0, len(pcm), SAMPLES_PER_10MS):
            chunk = pcm[i : i + SAMPLES_PER_10MS]
            if len(chunk) < SAMPLES_PER_10MS:
                chunk = np.pad(chunk, (0, SAMPLES_PER_10MS - len(chunk)))
            await self._queue.put(chunk.copy())


    async def push_wav(self, wav_bytes: bytes):
            """Push raw WAV bytes — strips header, resamples if needed, chunks."""
            import wave, io
            with wave.open(io.BytesIO(wav_bytes)) as wf:
                raw        = wf.readframes(wf.getnframes())
                src_rate   = wf.getframerate()
                src_ch     = wf.getnchannels()
                pcm        = np.frombuffer(raw, dtype=np.int16)

            if src_rate != SAMPLE_RATE or src_ch != CHANNELS:
                resampler = av.AudioResampler(
                    format   = "s16",
                    layout   = "mono",
                    rate     = SAMPLE_RATE,
                )
                # wrap raw bytes in an AudioFrame for the resampler
                frame             = AudioFrame.from_ndarray(pcm[None, :] if src_ch == 1 else pcm.reshape(src_ch, -1), layout="mono" if src_ch == 1 else "stereo")
                frame.sample_rate = src_rate
                frame.pts         = 0
                resampled_frames  = resampler.resample(frame)
                pcm = np.concatenate([
                    f.to_ndarray().flatten() for f in resampled_frames
                ]).astype(np.int16)

            await self.push_audio(pcm)


    async def push_pcm_bytes(self, raw_bytes: bytes):
        """Push raw PCM bytes — converts to int16 array and chunks."""
        await self.push_audio(np.frombuffer(raw_bytes, dtype=np.int16)) 

    
    def purge(self):
        """
        Optimized clear: Replaces the queue with a fresh one to drop 
        all buffered frames instantly.
        """
        items_dropped = self._queue.qsize()
        self._queue = asyncio.Queue(maxsize=50) 
        logger.info(f"AudioOutputTrack: Cleared {items_dropped} frames from buffer.")

    
    def clear(self):
        """Safely empties the queue without replacing the object."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        

    def stop(self):
        self._ended = True
        super().stop()