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
