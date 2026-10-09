"""Tests for the endpointer, level meter, resampler and Recorder (no real microphone)."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterable
from typing import Any

import numpy as np
import pytest

from voicepilot.audio import capture
from voicepilot.audio.capture import Recorder, convert_to_16k
from voicepilot.audio.vad import FRAME_BYTES, FRAME_MS, Decision, Endpointer, rms_level
from voicepilot.config import AppConfig
from voicepilot.contracts import AudioDeviceError, Mode

RATE = 16000
FRAME_SAMPLES = 480


def tone_samples(freq: float, seconds: float, rate: int, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(int(round(seconds * rate))) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)


def frames_of(samples: np.ndarray) -> list[bytes]:
    whole = len(samples) // FRAME_SAMPLES * FRAME_SAMPLES
    return [samples[i : i + FRAME_SAMPLES].astype("<i2").tobytes() for i in range(0, whole, FRAME_SAMPLES)]


def speech_frame() -> bytes:
    return (np.full(FRAME_SAMPLES, 3000, dtype="<i2")).tobytes()


def silence_frame() -> bytes:
    return bytes(FRAME_BYTES)


def loud(frame: bytes) -> bool:
    """Stand-in for webrtcvad: frames with any sample above 1000 count as speech."""
    return rms_level(frame) > 0.3


# ---------- endpointer ----------

def make_endpointer(silence_ms: int = 800, max_ms: int = 15000, no_speech_ms: int = 5000) -> Endpointer:
    return Endpointer(
        silence_end_ms=silence_ms,
        max_duration_ms=max_ms,
        no_speech_timeout_ms=no_speech_ms,
        is_speech=loud,
    )


def test_click_shorter_than_three_frames_does_not_start_speech() -> None:
    ep = make_endpointer(no_speech_ms=300)
    decisions = [ep.feed(speech_frame()), ep.feed(speech_frame())]
    assert decisions == [Decision.CONTINUE, Decision.CONTINUE]
    last = Decision.CONTINUE
    for _ in range(20):
        last = ep.feed(silence_frame())
        if last is not Decision.CONTINUE:
            break
    assert last is Decision.END_NO_SPEECH
    assert not ep.speech_started


def test_speech_then_silence_ends_after_silence_threshold() -> None:
    ep = make_endpointer(silence_ms=800)
    for _ in range(5):
        assert ep.feed(speech_frame()) is Decision.CONTINUE
    assert ep.speech_started
    needed = -(-800 // FRAME_MS)  # 27 frames of 30 ms
    decisions = [ep.feed(silence_frame()) for _ in range(needed)]
    assert decisions[:-1] == [Decision.CONTINUE] * (needed - 1)
    assert decisions[-1] is Decision.END_SILENCE
    assert ep.trailing_silence_ms >= 800


def test_preroll_keeps_300_ms_before_onset() -> None:
    ep = make_endpointer()
    for _ in range(20):
        ep.feed(silence_frame())
    for _ in range(3):
        ep.feed(speech_frame())
    assert ep.speech_started
    # 300 ms of pre-roll = 10 frames: 7 silence frames plus the 3 speech frames that started it.
    assert len(ep.audio()) == 10 * FRAME_BYTES
    assert ep.audio()[-FRAME_BYTES:] == speech_frame()


def test_speech_resets_trailing_silence() -> None:
    ep = make_endpointer(silence_ms=800)
    for _ in range(4):
        ep.feed(speech_frame())
    for _ in range(10):
        ep.feed(silence_frame())
    assert ep.trailing_silence_ms == 300
    ep.feed(speech_frame())
    assert ep.trailing_silence_ms == 0


def test_max_duration_ends_continuous_speech() -> None:
    ep = make_endpointer(max_ms=1000)
    decision = Decision.CONTINUE
    while decision is Decision.CONTINUE:
        decision = ep.feed(speech_frame())
    assert decision is Decision.END_MAX_DURATION
    assert ep.total_duration_ms == 1020  # first frame count at or above 1000 ms: 34 frames


def test_no_speech_timeout_for_silence() -> None:
    ep = make_endpointer(no_speech_ms=5000)
    decision = Decision.CONTINUE
    while decision is Decision.CONTINUE:
        decision = ep.feed(silence_frame())
    assert decision is Decision.END_NO_SPEECH
    assert ep.total_duration_ms == 5010  # first frame count at or above 5000 ms: 167 frames


def test_wrong_frame_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_endpointer().feed(b"\x00" * 10)


def test_rms_level_is_zero_for_silence_and_monotonic() -> None:
    assert rms_level(silence_frame()) == 0.0
    quiet = rms_level(np.full(FRAME_SAMPLES, 300, dtype="<i2").tobytes())
    medium = rms_level(np.full(FRAME_SAMPLES, 3000, dtype="<i2").tobytes())
    full = rms_level(np.full(FRAME_SAMPLES, 32767, dtype="<i2").tobytes())
    assert 0.0 < quiet < medium < full
    assert full == pytest.approx(1.0, abs=0.01)


# ---------- resampler ----------

def dominant_frequency(samples: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64) * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1.0 / rate)[int(np.argmax(spectrum))])


def test_one_second_at_48k_becomes_16000_samples() -> None:
    out = convert_to_16k(tone_samples(440, 1.0, 48000), 48000)
    assert out.dtype == np.int16
    assert abs(len(out) - 16000) <= 1


def test_440hz_keeps_its_frequency_after_48k_resampling() -> None:
    out = convert_to_16k(tone_samples(440, 1.0, 48000), 48000)
    assert dominant_frequency(out, RATE) == pytest.approx(440.0, abs=2.0)


def test_440hz_keeps_its_frequency_after_44100_resampling() -> None:
    out = convert_to_16k(tone_samples(440, 1.0, 44100), 44100)
    assert abs(len(out) - 16000) <= 1
    assert dominant_frequency(out, RATE) == pytest.approx(440.0, abs=2.0)


def test_content_above_speech_band_is_attenuated_when_downsampling() -> None:
    high = convert_to_16k(tone_samples(12000, 1.0, 48000), 48000)
    in_band = convert_to_16k(tone_samples(1000, 1.0, 48000), 48000)
    high_rms = np.sqrt(np.mean(high.astype(np.float64) ** 2))
    in_band_rms = np.sqrt(np.mean(in_band.astype(np.float64) ** 2))
    assert high_rms < 0.02 * in_band_rms


def test_amplitude_is_preserved_for_in_band_tone() -> None:
    src = tone_samples(440, 1.0, 48000, amplitude=0.5)
    out = convert_to_16k(src, 48000)
    expected_rms = 0.5 / np.sqrt(2) * 32767
    assert np.sqrt(np.mean(out.astype(np.float64) ** 2)) == pytest.approx(expected_rms, rel=0.03)


def test_streaming_chunks_match_one_shot_conversion() -> None:
    samples = tone_samples(440, 0.5, 48000)
    whole = convert_to_16k(samples, 48000)
    resampler = capture._Resampler(48000)
    pieces = []
    for i in range(0, len(samples), 1021):  # odd chunk size on purpose
        pieces.append(resampler.process(samples[i : i + 1021].astype(np.float64)))
    pieces.append(resampler.flush())
    streamed = np.rint(np.concatenate(pieces)).astype(np.int16)
    assert len(streamed) == len(whole)
    assert np.max(np.abs(streamed.astype(int) - whole.astype(int))) <= 1


def test_identity_rate_passes_through() -> None:
    samples = tone_samples(440, 0.25, RATE)
    out = convert_to_16k(samples, RATE)
    assert len(out) == len(samples)
    assert np.max(np.abs(out.astype(int) - samples.astype(int))) <= 1


# ---------- recorder ----------

class FakeStream:
    """Stands in for sounddevice.InputStream: delivers scripted chunks on start()."""

    def __init__(self, callback: Any, chunks: Iterable[np.ndarray], delivered: threading.Event) -> None:
        self._callback = callback
        self._chunks = list(chunks)
        self._delivered = delivered
        self.closed = False
        self.stopped = False

    def start(self) -> None:
        def pump() -> None:
            for chunk in self._chunks:
                self._callback(chunk.reshape(-1, 1), len(chunk), None, None)
            self._delivered.set()

        threading.Thread(target=pump, daemon=True).start()

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def chunked(samples: np.ndarray, size: int = 960) -> list[np.ndarray]:
    return [samples[i : i + size] for i in range(0, len(samples), size)]


def speech_samples(seconds: float, rate: int = RATE) -> np.ndarray:
    return np.full(int(seconds * rate), 3000, dtype=np.int16)


def silence_samples(seconds: float, rate: int = RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.int16)


def make_config(**overrides: Any) -> AppConfig:
    cfg = AppConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def scripted_recorder(
    config: AppConfig,
    chunks: list[np.ndarray],
    *,
    rate: int = RATE,
    on_level: Any = None,
    delivered: threading.Event | None = None,
) -> tuple[Recorder, threading.Event, list[FakeStream]]:
    done = delivered if delivered is not None else threading.Event()
    created: list[FakeStream] = []

    def factory(*, samplerate: int, device: Any, callback: Any) -> FakeStream:
        assert samplerate == rate
        stream = FakeStream(callback, chunks, done)
        created.append(stream)
        return stream

    recorder = Recorder(
        config,
        on_level=on_level,
        stream_factory=factory,
        input_rate=rate,
        is_speech=loud,
    )
    return recorder, done, created


def run_record(recorder: Recorder, mode: Mode = Mode.COMMAND, stop: threading.Event | None = None,
               cancel: threading.Event | None = None) -> Any:
    return recorder.record(3, mode, stop or threading.Event(), cancel or threading.Event())


def test_speech_then_silence_returns_clip_at_16k() -> None:
    chunks = chunked(np.concatenate([silence_samples(0.2), speech_samples(1.0), silence_samples(1.5)]))
    recorder, _, streams = scripted_recorder(make_config(silence_end_ms=800), chunks)
    clip = run_record(recorder)
    assert clip is not None
    assert clip.turn_id == 3
    assert clip.sample_rate == RATE
    assert 1.0 <= clip.duration_s <= 2.3
    assert streams[0].closed and streams[0].stopped


def test_48k_device_is_resampled_to_16k_clip() -> None:
    rate = 48000
    chunks = chunked(np.concatenate([speech_samples(1.0, rate), silence_samples(1.5, rate)]), size=1024)
    recorder, _, _ = scripted_recorder(make_config(silence_end_ms=800), chunks, rate=rate)
    clip = run_record(recorder)
    assert clip is not None
    assert clip.sample_rate == RATE
    assert 1.0 <= clip.duration_s <= 2.3


def test_silence_only_returns_none_after_no_speech_timeout() -> None:
    chunks = chunked(silence_samples(6.0))
    recorder, _, _ = scripted_recorder(make_config(no_speech_timeout_s=1), chunks)
    assert run_record(recorder) is None


def test_max_command_duration_ends_long_speech() -> None:
    chunks = chunked(speech_samples(3.0))
    recorder, _, _ = scripted_recorder(make_config(max_command_s=1), chunks)
    clip = run_record(recorder, Mode.COMMAND)
    assert clip is not None
    assert clip.duration_s == pytest.approx(1.0, abs=0.05)


def test_dictation_uses_its_own_duration_limit() -> None:
    chunks = chunked(speech_samples(3.0))
    recorder, _, _ = scripted_recorder(make_config(max_command_s=1, max_dictation_s=2), chunks)
    clip = run_record(recorder, Mode.DICTATION)
    assert clip is not None
    assert clip.duration_s == pytest.approx(2.0, abs=0.05)


def test_stop_event_after_speech_returns_what_we_have() -> None:
    chunks = chunked(speech_samples(0.8))
    stop = threading.Event()
    delivered = threading.Event()
    recorder, _, _ = scripted_recorder(make_config(silence_end_ms=800), chunks, delivered=delivered)

    def press_hotkey() -> None:
        assert delivered.wait(2.0)
        stop.set()

    threading.Thread(target=press_hotkey, daemon=True).start()
    clip = run_record(recorder, stop=stop)
    assert clip is not None
    assert clip.duration_s >= 0.7


def test_stop_event_before_speech_returns_none() -> None:
    chunks = chunked(silence_samples(0.5))
    stop = threading.Event()
    stop.set()
    recorder, _, _ = scripted_recorder(make_config(), chunks)
    assert run_record(recorder, stop=stop) is None


def test_cancel_returns_none_quickly() -> None:
    cancel = threading.Event()
    recorder, _, _ = scripted_recorder(make_config(), [])
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    begin = time.monotonic()
    try:
        assert run_record(recorder, cancel=cancel) is None
    finally:
        timer.cancel()
    assert time.monotonic() - begin < 0.5


def test_cancel_before_start_returns_none_without_opening_stream() -> None:
    cancel = threading.Event()
    cancel.set()
    recorder, _, streams = scripted_recorder(make_config(), chunks=[])
    assert run_record(recorder, cancel=cancel) is None
    assert streams == []


def test_stalled_device_raises_audio_device_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "_STALL_S", 0.1)
    recorder, _, _ = scripted_recorder(make_config(), chunks=[])
    with pytest.raises(AudioDeviceError, match="ngắt"):
        run_record(recorder)


def test_factory_failure_maps_to_vietnamese_device_error() -> None:
    def broken(**_: Any) -> None:
        raise OSError("PortAudio error -9996")

    recorder = Recorder(make_config(), stream_factory=broken, input_rate=RATE)
    with pytest.raises(AudioDeviceError) as info:
        run_record(recorder)
    assert str(info.value).startswith("Không")
    assert "micro" in str(info.value)


def test_start_failure_maps_to_device_error_and_closes_stream() -> None:
    closed: list[bool] = []

    class FailingStream:
        def start(self) -> None:
            raise RuntimeError("device busy")

        def stop(self) -> None:
            raise RuntimeError("never started")

        def close(self) -> None:
            closed.append(True)

    recorder = Recorder(make_config(), stream_factory=lambda **_: FailingStream(), input_rate=RATE)
    with pytest.raises(AudioDeviceError):
        run_record(recorder)
    assert closed == [True]


def test_missing_input_device_maps_to_device_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_device(device: Any) -> int:
        raise capture.AudioDeviceError("Không tìm thấy micro.")

    monkeypatch.setattr(capture, "_device_samplerate", no_device)
    recorder = Recorder(make_config(), stream_factory=lambda **_: FakeStream(None, [], threading.Event()))
    with pytest.raises(AudioDeviceError, match="micro"):
        run_record(recorder)


def test_on_level_is_called_with_normalized_levels() -> None:
    levels: queue.SimpleQueue[float] = queue.SimpleQueue()
    chunks = chunked(np.concatenate([speech_samples(0.5), silence_samples(1.2)]))
    recorder, _, _ = scripted_recorder(make_config(silence_end_ms=800), chunks, on_level=levels.put)
    run_record(recorder)
    collected = []
    while not levels.empty():
        collected.append(levels.get())
    assert collected
    assert all(0.0 <= v <= 1.0 for v in collected)
    assert max(collected) > 0.5


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        # sounddevice delivers int16 as shape (frames, 1); a 2-D int16 chunk must keep its scale.
        (np.full((480, 1), 1000, dtype=np.int16), 1000),
        (np.full((480, 2), 1000, dtype=np.int16), 1000),
        (np.full((480, 1), 0.5, dtype=np.float32), 16384),
        (np.full(480, 1000, dtype=np.int16), 1000),
    ],
)
def test_frame_source_keeps_int16_scale_for_device_shaped_chunks(chunk: np.ndarray, expected: int) -> None:
    (frame,) = capture._FrameSource(16000).push(chunk)

    samples = np.frombuffer(frame, dtype="<i2")
    assert samples.min() == samples.max() == expected
