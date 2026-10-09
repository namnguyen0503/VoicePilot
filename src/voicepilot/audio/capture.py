"""Microphone capture via sounddevice, resampled to mono 16 kHz PCM int16."""

from __future__ import annotations

import contextlib
import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from voicepilot.audio.vad import (
    FRAME_SAMPLES,
    SAMPLE_RATE,
    Decision,
    Endpointer,
    SpeechPredicate,
    rms_level,
)
from voicepilot.config import AppConfig
from voicepilot.contracts import AudioClip, AudioDeviceError, Mode

log = logging.getLogger(__name__)

_HALF_TAPS = 24  # windowed-sinc kernel half width, in input samples
_CUTOFF_FRACTION = 0.9  # passband edge as a fraction of the lower Nyquist frequency
_POLL_S = 0.05  # how often record() re-checks stop and cancel events
_STALL_S = 2.0  # no audio for this long means the device stopped delivering
_LEVEL_INTERVAL_S = 0.05
_NOISE_MARGIN_DB = 8.0  # speech must be this much louder than the background (real VAD only)


class InputStreamLike(Protocol):
    """The subset of sounddevice.InputStream that Recorder uses."""

    def start(self) -> None:
        """Start delivering audio to the callback."""
        ...

    def stop(self) -> None:
        """Stop delivering audio."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...


StreamFactory = Callable[..., InputStreamLike]


class _Resampler:
    """Streaming band-limited resampler from an arbitrary rate to 16 kHz.

    Each output sample is a Hann-windowed sinc interpolation around its
    fractional input position. The cutoff sits at 90 % of the lower Nyquist
    frequency, so downsampling from 44.1 or 48 kHz removes content that would
    alias into the speech band.
    """

    def __init__(self, src_rate: int, dst_rate: int = SAMPLE_RATE) -> None:
        self._passthrough = src_rate == dst_rate
        self._step = src_rate / dst_rate
        cutoff_hz = _CUTOFF_FRACTION * min(src_rate, dst_rate) / 2.0
        self._fc = cutoff_hz / src_rate
        self._offsets = np.arange(-_HALF_TAPS + 1, _HALF_TAPS + 1)
        self._buf = np.zeros(_HALF_TAPS, dtype=np.float64)  # left padding = silence
        self._buf_start = -_HALF_TAPS  # absolute input index of self._buf[0]
        self._next = 0.0  # absolute input position of the next output sample

    def process(self, x: np.ndarray) -> np.ndarray:
        """Feed float64 input samples and return the output samples that are ready."""
        if self._passthrough:
            return x
        self._buf = np.concatenate((self._buf, x))
        end = self._buf_start + len(self._buf)
        limit = end - _HALF_TAPS  # outputs need _HALF_TAPS samples of look-ahead
        count = max(0, math.ceil((limit - self._next) / self._step))
        if count == 0:
            return np.empty(0, dtype=np.float64)
        positions = self._next + self._step * np.arange(count)
        out = self._interpolate(positions)
        self._next = self._next + count * self._step
        keep_from = math.floor(self._next) - _HALF_TAPS + 1
        drop = keep_from - self._buf_start
        if drop > 0:
            self._buf = self._buf[drop:]
            self._buf_start += drop
        return out

    def flush(self) -> np.ndarray:
        """Emit the outputs still waiting for look-ahead by feeding silence."""
        if self._passthrough:
            return np.empty(0, dtype=np.float64)
        return self.process(np.zeros(_HALF_TAPS, dtype=np.float64))

    def _interpolate(self, positions: np.ndarray) -> np.ndarray:
        base = np.floor(positions).astype(np.int64)
        idx = base[:, None] + self._offsets[None, :]
        dist = idx - positions[:, None]
        kernel = (
            2.0
            * self._fc
            * np.sinc(2.0 * self._fc * dist)
            * (0.5 + 0.5 * np.cos(np.pi * dist / (_HALF_TAPS + 1)))
        )
        kernel /= kernel.sum(axis=1, keepdims=True)
        return np.einsum("ij,ij->i", self._buf[idx - self._buf_start], kernel)


def _mono_float(chunk: np.ndarray) -> np.ndarray:
    """Downmix to one channel and return samples on the int16 scale as float64."""
    data = np.asarray(chunk)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.floating):
        return data.astype(np.float64) * 32768.0
    return data.astype(np.float64)


def _to_int16(samples: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(samples), -32768, 32767).astype(np.int16)


def convert_to_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    """Convert one complete recording to mono 16 kHz int16 (used for tests and offline files)."""
    resampler = _Resampler(rate)
    out = np.concatenate((resampler.process(_mono_float(samples)), resampler.flush()))
    return _to_int16(out)


class _FrameSource:
    """Turns device chunks into 30 ms mono 16 kHz int16 frames."""

    def __init__(self, rate: int) -> None:
        self._resampler = _Resampler(rate)
        self._pending = np.empty(0, dtype=np.int16)

    def push(self, chunk: np.ndarray) -> list[bytes]:
        """Convert one device chunk and return the complete frames it produced."""
        converted = _to_int16(self._resampler.process(_mono_float(chunk)))
        self._pending = np.concatenate((self._pending, converted))
        whole = len(self._pending) // FRAME_SAMPLES * FRAME_SAMPLES
        frames = [
            self._pending[i : i + FRAME_SAMPLES].tobytes() for i in range(0, whole, FRAME_SAMPLES)
        ]
        self._pending = self._pending[whole:]
        return frames


def _device_samplerate(device: int | None) -> int:
    """Return the default sample rate of the input device."""
    try:
        import sounddevice as sd  # native dependency, imported only when recording

        info = sd.query_devices(device, kind="input")
        return round(float(info["default_samplerate"]))
    except Exception as exc:
        raise AudioDeviceError("Không tìm thấy micro.") from exc


def _sounddevice_stream(
    *, samplerate: int, device: int | None, callback: Callable[..., None]
) -> InputStreamLike:
    """Open a mono int16 sounddevice input stream."""
    import sounddevice as sd

    return sd.InputStream(
        samplerate=samplerate,
        device=device,
        channels=1,
        dtype="int16",
        callback=callback,
    )


def _always_speech(frame: bytes) -> bool:
    return True


class Recorder:
    """Records one utterance per call, ending on silence, stop, cancel or timeout.

    record() blocks and is meant to run on a worker thread. The audio callback
    only copies the buffer into a queue; resampling, endpointing and metering
    run on the thread that calls record().
    """

    def __init__(
        self,
        config: AppConfig,
        on_level: Callable[[float], None] | None = None,
        device: int | None = None,
        stream_factory: StreamFactory | None = None,
        *,
        input_rate: int | None = None,
        is_speech: SpeechPredicate | None = None,
    ) -> None:
        """Create a recorder.

        Args:
            config: Supplies silence, duration and no-speech limits.
            on_level: Receives the meter level (0..1) about every 50 ms.
            device: sounddevice input device index, or None for the default.
            stream_factory: Test seam. Called as factory(samplerate=, device=, callback=)
                and must return an object with start(), stop() and close().
            input_rate: Device sample rate. Queried from sounddevice when None.
            is_speech: Speech predicate for 30 ms frames. Defaults to webrtcvad.
        """
        self._config = config
        self._on_level = on_level
        self._device = device
        self._stream_factory = stream_factory or _sounddevice_stream
        self._input_rate = input_rate
        self._is_speech = is_speech

    def record(
        self,
        turn_id: int,
        mode: Mode,
        stop_event: threading.Event,
        cancel_event: threading.Event,
    ) -> AudioClip | None:
        """Capture one utterance.

        Returns:
            The clip, or None when cancelled, when no speech was detected, or when
            stop_event was set before speech started.

        Raises:
            AudioDeviceError: When there is no microphone or the stream fails.
        """
        if cancel_event.is_set():
            return None
        max_s = self._config.max_dictation_s if mode is Mode.DICTATION else self._config.max_command_s
        rate = self._input_rate if self._input_rate is not None else _device_samplerate(self._device)
        chunks: queue.SimpleQueue[np.ndarray] = queue.SimpleQueue()
        use_vad = self._config.vad_enabled
        endpointer = Endpointer(
            silence_end_ms=self._config.silence_end_ms,
            max_duration_ms=max_s * 1000,
            no_speech_timeout_ms=round(self._config.no_speech_timeout_s * 1000),
            # VAD off: every frame counts as speech, so the hotkey (stop) or max duration ends the clip.
            is_speech=self._is_speech if use_vad else _always_speech,
            noise_margin_db=_NOISE_MARGIN_DB if use_vad and self._is_speech is None else None,
        )
        source = _FrameSource(rate)
        stream = self._open(rate, chunks.put)
        try:
            try:
                stream.start()
            except Exception as exc:
                raise AudioDeviceError("Không mở được micro.") from exc
            self._pump(chunks, source, endpointer, stop_event, cancel_event)
        finally:
            with contextlib.suppress(Exception):  # a stream that never started may refuse to stop
                stream.stop()
            stream.close()

        if cancel_event.is_set() or not endpointer.speech_started:
            return None
        return AudioClip(samples=endpointer.audio(), turn_id=turn_id)

    def _open(self, rate: int, on_chunk: Callable[[np.ndarray], None]) -> InputStreamLike:
        def callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
            on_chunk(np.array(indata, copy=True))

        try:
            return self._stream_factory(samplerate=rate, device=self._device, callback=callback)
        except Exception as exc:
            raise AudioDeviceError("Không mở được micro.") from exc

    def _pump(
        self,
        chunks: queue.SimpleQueue[np.ndarray],
        source: _FrameSource,
        endpointer: Endpointer,
        stop_event: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        """Feed frames to the endpointer until it decides, stop is requested, or cancel is requested.

        After a stop request, audio that is already queued is still processed, so
        speech spoken just before the hotkey press is not lost.
        """
        last_data = time.monotonic()
        level_at = float("-inf")
        stopping = False
        while not cancel_event.is_set():
            stopping = stopping or stop_event.is_set()
            try:
                chunk = chunks.get_nowait() if stopping else chunks.get(timeout=_POLL_S)
            except queue.Empty:
                if stopping:
                    return
                if time.monotonic() - last_data > _STALL_S:
                    raise AudioDeviceError("Micro bị ngắt giữa chừng.") from None
                continue
            last_data = time.monotonic()
            for frame in source.push(chunk):
                now = time.monotonic()
                if self._on_level is not None and now - level_at >= _LEVEL_INTERVAL_S:
                    level_at = now
                    self._on_level(rms_level(frame))
                if endpointer.feed(frame) is not Decision.CONTINUE:
                    return
