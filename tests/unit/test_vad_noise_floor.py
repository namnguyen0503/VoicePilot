"""Regression: a hot microphone (steady -37 dBFS noise) made webrtcvad report speech on every frame."""

from __future__ import annotations

import numpy as np

from voicepilot.audio.vad import FRAME_SAMPLES, Decision, Endpointer


def tone(amplitude: int) -> bytes:
    sign = np.where(np.arange(FRAME_SAMPLES) % 2 == 0, 1, -1)
    return (sign * amplitude).astype("<i2").tobytes()


def noise() -> bytes:
    return tone(460)  # about -37 dBFS


def speech() -> bytes:
    return tone(4000)  # about -18 dBFS


def always_speech(frame: bytes) -> bool:
    return True


def make(margin: float | None = 8.0) -> Endpointer:
    return Endpointer(
        silence_end_ms=800,
        max_duration_ms=15000,
        no_speech_timeout_ms=5000,
        is_speech=always_speech,
        noise_margin_db=margin,
    )


def run(ep: Endpointer, frames: list[bytes]) -> Decision:
    last = Decision.CONTINUE
    for frame in frames:
        last = ep.feed(frame)
        if last is not Decision.CONTINUE:
            break
    return last


def test_steady_noise_never_counts_as_speech() -> None:
    ep = make()
    assert run(ep, [noise()] * 300) is Decision.END_NO_SPEECH
    assert not ep.speech_started


def test_speech_over_noise_ends_on_trailing_noise() -> None:
    ep = make()
    frames = [noise()] * 10 + [speech()] * 20 + [noise()] * 60
    assert run(ep, frames) is Decision.END_SILENCE
    assert ep.speech_started
    assert ep.total_duration_ms < 15000


def test_without_margin_the_old_behaviour_treats_noise_as_speech() -> None:
    ep = make(margin=None)
    run(ep, [noise()] * 30)
    assert ep.speech_started


def test_recorder_with_vad_disabled_records_until_stopped() -> None:
    import threading

    from voicepilot.audio.capture import Recorder
    from voicepilot.config import AppConfig
    from voicepilot.contracts import Mode

    stop = threading.Event()

    class Stream:
        def __init__(self, callback):
            self.callback = callback

        def start(self) -> None:
            # 1 s of steady noise, then the user presses the hotkey again.
            for _ in range(10):
                self.callback(np.full((1600, 1), 460, dtype=np.int16), 1600, None, None)
            stop.set()

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    config = AppConfig(vad_enabled=False)
    recorder = Recorder(config, input_rate=16000, stream_factory=lambda **kw: Stream(kw["callback"]))
    clip = recorder.record(1, Mode.COMMAND, stop, threading.Event())
    assert clip is not None
    assert len(clip.samples) >= 16000 * 2 * 0.9
