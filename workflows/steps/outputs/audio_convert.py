import asyncio
import logging
import sys

import numpy as np
from .output_expect_format import AudioFormat, Encoding, _DTYPE_MAP
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator
import av



class AudioConverter:
    """
    Stateful streaming PCM audio converter.

    The converter is intended for ONE continuous audio stream.
    Call reset() before reusing it for another stream.

    Conversion pipeline:

        raw bytes
            ↓
        PCM ndarray
            ↓
        AudioFrame
            ↓
        PyAV AudioResampler
            ↓
        target PCM bytes

    PyAV's AudioResampler maintains resampling state between calls.
    """

    def __init__(
        self,
        input_format: AudioFormat,
        output_format: AudioFormat,
    ):

        if input_format is None or output_format is None:
            raise ValueError(
            f"AudioConverter requires non-None formats, got "
            f"input_format={input_format!r}, output_format={output_format!r}"
            )

        self.input_format = input_format
        self.output_format = output_format

        self._leftover = b""

        self._resampler: av.AudioResampler | None = None

        self._create_resampler()

    def _create_resampler(self) -> None:
        """
        Create the stateful PyAV resampler.

        It is created ONCE and reused for the entire stream.
        """

        needs_resampling = (
            self.input_format.sample_rate
            != self.output_format.sample_rate
            or self.input_format.channels
            != self.output_format.channels
            or self.input_format.encoding
            != self.output_format.encoding
        )

        if not needs_resampling:
            self._resampler = None
            return

        # PyAV AudioResampler handles:
        #
        # sample rate
        # channel layout
        # sample format
        #
        self._resampler = av.AudioResampler(
            format=self._pyav_format(self.output_format.encoding),
            layout=self._channel_layout(self.output_format.channels),
            rate=self.output_format.sample_rate,
        )

    @staticmethod
    def _pyav_format(encoding: str) -> str:
        """
        Convert our Encoding enum/string to PyAV audio format.
        """

        mapping = {
            Encoding.PCM_S16LE: "s16",
            Encoding.PCM_F32LE: "flt",
            Encoding.PCM_S32LE: "s32",
            Encoding.PCM_U8: "u8",
            Encoding.PCM_S24LE: "s32",
        }

        return mapping[encoding]

    @staticmethod
    def _channel_layout(channels: int) -> str:
        if channels == 1:
            return "mono"

        if channels == 2:
            return "stereo"

        raise ValueError(
            f"Unsupported channel count for PyAV: {channels}"
        )

    def reset(self) -> None:
        """
        Reset the converter for a new independent stream.
        """

        self._leftover = b""
        self._create_resampler()

    def convert(self, raw: bytes) -> np.ndarray:
        """
        Convert one streaming PCM chunk.

        Always returns a numpy array containing PCM samples.
        """

        if not raw:
            return np.empty(0, dtype=self._output_dtype())

        # ---------------------------------------------------------
        # 1. Handle bytes left over from previous chunk
        # ---------------------------------------------------------

        data = self._leftover + raw

        itemsize = self._item_size(
            self.input_format.encoding
        )

        usable_len = len(data) - (len(data) % itemsize)

        usable = data[:usable_len]
        self._leftover = data[usable_len:]

        if not usable:
            return np.empty(0, dtype=self._output_dtype())

        # ---------------------------------------------------------
        # 2. Identity conversion
        # ---------------------------------------------------------

        if self.input_format == self.output_format:
            return self._bytes_to_array(usable)

        # ---------------------------------------------------------
        # 3. bytes → ndarray
        # ---------------------------------------------------------

        arr = self._bytes_to_array(usable)

        # ---------------------------------------------------------
        # 4. ndarray → AudioFrame
        # ---------------------------------------------------------

        frame = self._array_to_audio_frame(arr)

        # ---------------------------------------------------------
        # 5. Stateful PyAV conversion
        # ---------------------------------------------------------

        assert self._resampler is not None

        output_frames = self._resampler.resample(frame)

        if not output_frames:
            return np.empty(0, dtype=self._output_dtype())

        # ---------------------------------------------------------
        # 6. AudioFrames → ndarray
        # ---------------------------------------------------------

        return self._frames_to_pcm(output_frames)

    def flush(self) -> np.ndarray:
        """
        Flush the stateful resampler at the end of the stream.
        """

        self._leftover = b""

        if self._resampler is None:
            return np.empty(0, dtype=self._output_dtype())

        output_frames = self._resampler.resample(None)

        if not output_frames:
            return np.empty(0, dtype=self._output_dtype())

        return self._frames_to_pcm(output_frames)

    # =============================================================
    # PCM bytes → ndarray
    # =============================================================

    def _output_dtype(self):
        if self.output_format.encoding == Encoding.PCM_S24LE:
            return np.dtype(np.int32)

        return np.dtype(
            _DTYPE_MAP[self.output_format.encoding]
        )

    def _item_size(self, encoding: str) -> int:
        if encoding == Encoding.PCM_S24LE:
            return 3

        return np.dtype(
            _DTYPE_MAP[encoding]
        ).itemsize

    def _bytes_to_array(self, data: bytes) -> np.ndarray:

        encoding = self.input_format.encoding

        if encoding == Encoding.PCM_S24LE:
            return self._pcm24_bytes_to_array(data)

        dtype = _DTYPE_MAP[encoding]

        return np.frombuffer(
            data,
            dtype=dtype,
        ).copy()

    # =============================================================
    # ndarray → AudioFrame
    # =============================================================

    def _array_to_audio_frame(
        self,
        arr: np.ndarray,
    ) -> av.AudioFrame:

        channels = self.input_format.channels
        fmt = self._pyav_format(self.input_format.encoding)
        layout = self._channel_layout(channels)

        # arr coming from _bytes_to_array() is interleaved:
        #
        # mono:
        #   [L L L L ...]
        #
        # stereo:
        #   [L R L R L R ...]
        #
        # PyAV packed formats such as "s16" expect:
        #
        #   (1, total_interleaved_values)
        #
        if channels > 1:
            arr = arr.reshape(-1, channels)

        packed = arr.reshape(1, -1)

        frame = av.AudioFrame.from_ndarray(
            packed,
            format=fmt,
            layout=layout,
        )

        frame.sample_rate = self.input_format.sample_rate

        return frame

    # =============================================================
    # AudioFrames → output bytes
    # =============================================================

    def _frames_to_bytes(
        self,
        frames: list[av.AudioFrame],
    ) -> bytes:

            if not frames:
                return b""

            arrays = []

            for frame in frames:
                arr = frame.to_ndarray()

                # For packed audio ("s16"), PyAV returns:
                #
                # mono   -> (1, samples)
                # stereo -> (1, samples * channels)
                #
                # Therefore flattening gives exactly the raw
                # interleaved PCM representation we need.

                arrays.append(arr.reshape(-1))

            return np.concatenate(arrays).tobytes()

    def _frames_to_pcm(
        self,
        frames: list[av.AudioFrame],
    ) -> np.ndarray:

        if not frames:
            return np.empty(0, dtype=self._output_dtype())

        arrays = []

        for frame in frames:
            arr = frame.to_ndarray()
            arrays.append(arr.reshape(-1))

        return np.concatenate(arrays)

    # =============================================================
    # ndarray → bytes
    # =============================================================

    def _array_to_bytes(
        self,
        arr: np.ndarray,
        encoding: str,
    ) -> bytes:

        if encoding == Encoding.PCM_S24LE:
            return self._array_to_pcm24_bytes(arr)

        return arr.tobytes()

    # =============================================================
    # PCM24
    # =============================================================

    @staticmethod
    def _pcm24_bytes_to_array(
        raw: bytes,
    ) -> np.ndarray:

        u8 = np.frombuffer(
            raw,
            dtype=np.uint8,
        ).reshape(-1, 3)

        val = (
            u8[:, 0].astype(np.int32)
            | (u8[:, 1].astype(np.int32) << 8)
            | (u8[:, 2].astype(np.int32) << 16)
        )

        return np.where(
            val & 0x800000,
            val | ~0xFFFFFF,
            val,
        )

    @staticmethod
    def _array_to_pcm24_bytes(
        arr: np.ndarray,
    ) -> bytes:

        arr = arr.astype(np.int32) & 0xFFFFFF

        out = bytearray(len(arr) * 3)

        out[0::3] = (
            (arr & 0xFF)
            .astype(np.uint8)
            .tobytes()
        )

        out[1::3] = (
            ((arr >> 8) & 0xFF)
            .astype(np.uint8)
            .tobytes()
        )

        out[2::3] = (
            ((arr >> 16) & 0xFF)
            .astype(np.uint8)
            .tobytes()
        )

        return bytes(out)



# ─── Test ─────────────────────────────────────────────────────────────────────
# docker exec -it worker-1 python3 -m workflows.steps.outputs.audio_convert

logger = logging.getLogger(__name__)

_results: list[bool] = []


def _check(name: str, condition: bool, detail: str = "") -> bool:
    _results.append(condition)

    if condition:
        logger.info("PASS: %s", name)
    else:
        logger.error(
            "FAIL: %s%s",
            name,
            f" — {detail}" if detail else "",
        )

    return condition


def _make_tone(
    freq_hz: float,
    duration_s: float,
    sample_rate: int,
    channels: int = 1,
    dtype=np.int16,
    amplitude: float = 0.5,
) -> bytes:
    """Generate interleaved PCM bytes."""

    n_samples = int(duration_s * sample_rate)

    t = np.arange(n_samples) / sample_rate

    tone = np.sin(2 * np.pi * freq_hz * t) * amplitude

    if channels > 1:
        tone = np.tile(tone[:, None], (1, channels))

    if dtype == np.int16:
        arr = (tone * 32767).astype(np.int16)

    elif dtype == np.int32:
        arr = (tone * 2147483647).astype(np.int32)

    elif dtype == np.float32:
        arr = tone.astype(np.float32)

    elif dtype == np.uint8:
        arr = ((tone * 127) + 128).astype(np.uint8)

    else:
        raise ValueError(dtype)

    return arr.reshape(-1).tobytes()


def _chunk_bytes(data: bytes, chunk_size: int):
    """Split bytes into fixed-size pieces, simulating network chunks."""

    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]


def _concat_pcm(chunks: list[np.ndarray]) -> np.ndarray:
    """
    Concatenate PCM arrays returned by AudioConverter.

    Empty arrays are ignored.
    """

    valid = [
        chunk
        for chunk in chunks
        if chunk is not None and chunk.size > 0
    ]

    if not valid:
        return np.empty(0, dtype=np.float32)

    return np.concatenate(valid)


# ------------------------------------------------------------------------------
# Passthrough
# ------------------------------------------------------------------------------

async def _test_passthrough() -> None:
    logger.info("--- identity conversion is a passthrough ---")

    fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    conv = AudioConverter(fmt, fmt)

    raw = _make_tone(
        440,
        0.02,
        48000,
        channels=1,
        dtype=np.int16,
    )

    expected = np.frombuffer(raw, dtype=np.int16)

    out = conv.convert(raw)
    flushed = conv.flush()

    # No resampling/conversion should be necessary.
    _check(
        "passthrough returns numpy array",
        isinstance(out, np.ndarray),
        f"got {type(out)}",
    )

    _check(
        "passthrough dtype is int16",
        out.dtype == np.int16,
        f"got {out.dtype}",
    )

    _check(
        "passthrough samples identical",
        np.array_equal(out, expected),
    )

    _check(
        "passthrough flush is empty",
        flushed.size == 0,
        f"got {flushed.size} samples",
    )

    _check(
        "passthrough sample count is correct",
        len(out) == 960,
        f"got {len(out)} samples",
    )


# ------------------------------------------------------------------------------
# Upsample
# ------------------------------------------------------------------------------

async def _test_upsample() -> None:
    logger.info("--- resample 24kHz -> 48kHz ---")

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=24000,
        channels=1,
        sample_width=2,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    conv = AudioConverter(src_fmt, dst_fmt)

    raw = _make_tone(
        440,
        0.5,
        24000,
        channels=1,
        dtype=np.int16,
    )

    out = conv.convert(raw)
    flushed = conv.flush()

    # Stateful resampler may keep samples internally.
    total = _concat_pcm([out, flushed])

    expected_len = 24000

    _check(
        "upsample returns numpy array",
        isinstance(total, np.ndarray),
        f"got {type(total)}",
    )

    _check(
        "upsample output dtype is int16",
        total.dtype == np.int16,
        f"got {total.dtype}",
    )

    _check(
        "upsampled sample count",
        abs(len(total) - expected_len) <= 50,
        f"got {len(total)}, expected ~{expected_len}",
    )

    _check(
        "output amplitude in valid int16 range",
        total.max() <= 32767
        and total.min() >= -32768,
    )


# ------------------------------------------------------------------------------
# Downsample
# ------------------------------------------------------------------------------

async def _test_downsample() -> None:
    logger.info("--- resample 48kHz -> 16kHz ---")

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )

    conv = AudioConverter(src_fmt, dst_fmt)

    raw = _make_tone(
        440,
        0.3,
        48000,
        channels=1,
        dtype=np.int16,
    )

    out = conv.convert(raw)
    flushed = conv.flush()

    total = _concat_pcm([out, flushed])

    expected_len = 4800

    _check(
        "downsampled sample count",
        abs(len(total) - expected_len) <= 50,
        f"got {len(total)}, expected ~{expected_len}",
    )

    _check(
        "downsample output dtype is int16",
        total.dtype == np.int16,
        f"got {total.dtype}",
    )


# ------------------------------------------------------------------------------
# Mono -> Stereo
# ------------------------------------------------------------------------------

async def _test_mono_to_stereo() -> None:
    logger.info("--- mono -> stereo channel duplication ---")

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=2,
        sample_width=2,
    )

    conv = AudioConverter(src_fmt, dst_fmt)

    raw = _make_tone(
        440,
        0.02,
        48000,
        channels=1,
        dtype=np.int16,
    )

    out = conv.convert(raw)
    flushed = conv.flush()

    total = _concat_pcm([out, flushed])

    _check(
        "stereo output has even number of samples",
        len(total) % 2 == 0,
    )

    stereo = total.reshape(-1, 2)

    _check(
        "stereo output has 2 channels",
        stereo.shape[1] == 2,
    )

    _check(
        "both channels are identical",
        np.array_equal(
            stereo[:, 0],
            stereo[:, 1],
        ),
    )


# ------------------------------------------------------------------------------
# Stereo -> Mono
# ------------------------------------------------------------------------------

async def _test_stereo_to_mono() -> None:
    logger.info("--- stereo -> mono downmix (averaging) ---")

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=2,
        sample_width=2,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    conv = AudioConverter(src_fmt, dst_fmt)

    n = 960

    left = np.full(
        n,
        10000,
        dtype=np.int16,
    )

    right = np.full(
        n,
        -10000,
        dtype=np.int16,
    )

    stereo = np.empty(
        (n, 2),
        dtype=np.int16,
    )

    stereo[:, 0] = left
    stereo[:, 1] = right

    raw = stereo.reshape(-1).tobytes()

    out = conv.convert(raw)
    flushed = conv.flush()

    total = _concat_pcm([out, flushed])

    _check(
        "mono output has expected sample count",
        len(total) == n,
        f"got {len(total)}, expected {n}",
    )

    _check(
        "mono downmix of +10000/-10000 is ~0",
        np.allclose(total, 0, atol=2),
        f"got mean={total.mean()}",
    )


# ------------------------------------------------------------------------------
# Encoding conversion
# ------------------------------------------------------------------------------

async def _test_encoding_conversion() -> None:
    logger.info("--- encoding conversion int16 -> float32 ---")

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_F32LE,
        sample_rate=48000,
        channels=1,
        sample_width=4,
    )

    conv = AudioConverter(src_fmt, dst_fmt)

    raw = _make_tone(
        440,
        0.02,
        48000,
        channels=1,
        dtype=np.int16,
    )

    out = conv.convert(raw)
    flushed = conv.flush()

    total = _concat_pcm([out, flushed])

    _check(
        "float32 output is numpy array",
        isinstance(total, np.ndarray),
    )

    _check(
        "float32 output dtype is float32",
        total.dtype == np.float32,
        f"got {total.dtype}",
    )

    _check(
        "float32 output sample count is correct",
        len(total) == 960,
        f"got {len(total)} samples",
    )

    _check(
        "float32 values within [-1, 1]",
        total.max() <= 1.0
        and total.min() >= -1.0,
    )


# ------------------------------------------------------------------------------
# Chunked streaming
# ------------------------------------------------------------------------------

async def _test_chunked_streaming() -> None:
    logger.info(
        "--- chunked/streaming input with mid-sample splits ---"
    )

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
        container="raw",
    )

    raw = _make_tone(
        880,
        0.1,
        48000,
        channels=1,
        dtype=np.int16,
    )

    # Stateful converter receiving network-like chunks.
    conv_chunked = AudioConverter(src_fmt, dst_fmt)

    chunks: list[np.ndarray] = []

    for piece in _chunk_bytes(raw, 7):
        pcm = conv_chunked.convert(piece)

        if pcm.size:
            chunks.append(pcm)

    final_pcm = conv_chunked.flush()

    if final_pcm.size:
        chunks.append(final_pcm)

    reassembled = _concat_pcm(chunks)

    expected = np.frombuffer(raw, dtype=np.int16)

    _check(
        "chunked output is numpy array",
        isinstance(reassembled, np.ndarray),
    )

    _check(
        "chunked reassembly preserves sample count",
        len(reassembled) == len(expected),
        f"got {len(reassembled)}, expected {len(expected)}",
    )

    _check(
        "chunked reassembly matches original",
        np.array_equal(reassembled, expected),
    )

    # Compare with single-shot conversion.
    conv_single = AudioConverter(src_fmt, dst_fmt)

    single = conv_single.convert(raw)
    single_flush = conv_single.flush()

    single_pcm = _concat_pcm([
        single,
        single_flush,
    ])

    _check(
        "chunked output matches single-shot output",
        np.array_equal(
            reassembled,
            single_pcm,
        ),
    )


# ------------------------------------------------------------------------------
# Stateful resampling with TTS-like chunks
# ------------------------------------------------------------------------------

async def _test_streaming_upsample() -> None:
    logger.info("--- streaming 24kHz -> 48kHz ---")

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=24000,
        channels=1,
        sample_width=2,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    raw = _make_tone(
        440,
        0.5,
        24000,
        channels=1,
        dtype=np.int16,
    )

    conv = AudioConverter(
        src_fmt,
        dst_fmt,
    )

    output_chunks: list[np.ndarray] = []

    # Simulate HTTP/TTS streaming chunks.
    for chunk in _chunk_bytes(raw, 137):

        pcm = conv.convert(chunk)

        if pcm.size:
            output_chunks.append(pcm)

    # Flush the stateful resampler at the end.
    pcm = conv.flush()

    if pcm.size:
        output_chunks.append(pcm)

    output = _concat_pcm(output_chunks)

    expected = 24000

    _check(
        "streaming output is numpy array",
        isinstance(output, np.ndarray),
    )

    _check(
        "streaming output dtype is int16",
        output.dtype == np.int16,
        f"got {output.dtype}",
    )

    _check(
        "streaming 24k -> 48k sample count",
        abs(len(output) - expected) <= 50,
        f"got {len(output)}, expected {expected}",
    )


# ------------------------------------------------------------------------------
# Reset
# ------------------------------------------------------------------------------

async def _test_reset() -> None:
    logger.info("--- leftover state does not leak across reset() ---")

    fmt_a = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    fmt_b = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=44100,
        channels=1,
        sample_width=2,
    )

    conv = AudioConverter(fmt_a, fmt_b)

    conv.convert(b"\x01")

    _check(
        "leftover populated before reset",
        conv._leftover == b"\x01",
    )

    conv.reset()

    _check(
        "leftover cleared after reset",
        conv._leftover == b"",
    )


# ------------------------------------------------------------------------------
# PCM U8 -> S16
# ------------------------------------------------------------------------------

async def _test_pcm_u8_roundtrip() -> None:
    logger.info("--- pcm_u8 -> pcm_s16le roundtrip ---")

    src_fmt = AudioFormat(
        encoding=Encoding.PCM_U8,
        sample_rate=48000,
        channels=1,
        sample_width=1,
    )

    dst_fmt = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    conv = AudioConverter(src_fmt, dst_fmt)

    u8_raw = bytes([0, 128, 255])

    out = conv.convert(u8_raw)
    flushed = conv.flush()

    total = _concat_pcm([
        out,
        flushed,
    ])

    _check(
        "u8 output dtype is int16",
        total.dtype == np.int16,
        f"got {total.dtype}",
    )

    _check(
        "u8=0 maps to negative int16",
        total[0] < -30000,
        f"got {total[0]}",
    )

    _check(
        "u8=128 maps near zero",
        abs(int(total[1])) < 500,
        f"got {total[1]}",
    )

    _check(
        "u8=255 maps to positive int16",
        total[2] > 30000,
        f"got {total[2]}",
    )


# ------------------------------------------------------------------------------
# Empty input
# ------------------------------------------------------------------------------

async def _test_empty_input() -> None:
    logger.info("--- empty input returns empty numpy array ---")

    fmt_a = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=48000,
        channels=1,
        sample_width=2,
    )

    fmt_b = AudioFormat(
        encoding=Encoding.PCM_S16LE,
        sample_rate=44100,
        channels=1,
        sample_width=2,
    )

    conv = AudioConverter(fmt_a, fmt_b)

    out = conv.convert(b"")

    _check(
        "empty input returns numpy array",
        isinstance(out, np.ndarray),
    )

    _check(
        "empty input returns empty array",
        out.size == 0,
        f"got {out.size} samples",
    )


# ------------------------------------------------------------------------------
# Run all tests
# ------------------------------------------------------------------------------

async def _test() -> None:

    _results.clear()

    await _test_passthrough()
    await _test_upsample()
    await _test_downsample()
    await _test_mono_to_stereo()
    await _test_stereo_to_mono()
    await _test_encoding_conversion()
    await _test_chunked_streaming()
    await _test_reset()
    await _test_pcm_u8_roundtrip()
    await _test_empty_input()
    await _test_streaming_upsample()

    passed = sum(_results)
    total = len(_results)

    if all(_results):
        logger.info(
            "RESULT: %d/%d checks passed",
            passed,
            total,
        )
    else:
        logger.error(
            "RESULT: %d/%d checks passed",
            passed,
            total,
        )
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_test())

