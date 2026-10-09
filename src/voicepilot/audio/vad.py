"""Voice activity detection: pure end-of-utterance logic plus frame level metering.

The Endpointer is fed fixed 30 ms frames of 16 kHz mono int16 PCM. The speech
predicate is injected so the logic can be tested without webrtcvad; the default
is webrtcvad in aggressiveness mode 2, imported lazily.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from enum import Enum

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
FRAME_BYTES = FRAME_SAMPLES * 2  # int16 mono
PREROLL_MS = 300
MIN_SPEECH_FRAMES = 3  # 90 ms of consecutive speech before speech counts as started
WEBRTC_AGGRESSIVENESS = 2

SpeechPredicate = Callable[[bytes], bool]


class Decision(Enum):
    """Result of feeding one frame to the Endpointer."""

    CONTINUE = "continue"
    END_SILENCE = "end_silence"
    END_MAX_DURATION = "end_max_duration"
    END_NO_SPEECH = "end_no_speech"


def webrtc_is_speech(aggressiveness: int = WEBRTC_AGGRESSIVENESS) -> SpeechPredicate:
    """Build a speech predicate backed by webrtcvad (imported lazily)."""
    import webrtcvad  # native dependency, imported only when a real predicate is needed

    vad = webrtcvad.Vad(aggressiveness)
    return lambda frame: vad.is_speech(frame, SAMPLE_RATE)


class Endpointer:
    """Decides when an utterance starts and ends, frame by frame.

    Speech starts after MIN_SPEECH_FRAMES consecutive speech frames, so a click
    does not count. Frames from the last PREROLL_MS before onset are kept so the
    first syllable is not cut. The utterance ends after ``silence_end_ms`` of
    trailing silence, at ``max_duration_ms`` total, or, if speech never started,
    at ``no_speech_timeout_ms``.
    """

    def __init__(
        self,
        *,
        silence_end_ms: int,
        max_duration_ms: int,
        no_speech_timeout_ms: int,
        is_speech: SpeechPredicate | None = None,
    ) -> None:
        self._silence_end_ms = silence_end_ms
        self._max_duration_ms = max_duration_ms
        self._no_speech_timeout_ms = no_speech_timeout_ms
        self._is_speech = is_speech if is_speech is not None else webrtc_is_speech()
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_MS // FRAME_MS)
        self._frames: list[bytes] = []
        self._speech_run = 0
        self._started = False
        self._total_ms = 0
        self._trailing_ms = 0

    @property
    def speech_started(self) -> bool:
        """True once MIN_SPEECH_FRAMES consecutive speech frames were seen."""
        return self._started

    @property
    def trailing_silence_ms(self) -> int:
        """Milliseconds of silence since the last speech frame (0 before speech starts)."""
        return self._trailing_ms

    @property
    def total_duration_ms(self) -> int:
        """Milliseconds of audio fed so far."""
        return self._total_ms

    def audio(self) -> bytes:
        """Return the captured utterance: pre-roll, speech and trailing silence."""
        return b"".join(self._frames)

    def feed(self, frame: bytes) -> Decision:
        """Process one 30 ms frame and return the decision for it.

        Raises:
            ValueError: If the frame is not FRAME_BYTES long.
        """
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"frame must be {FRAME_BYTES} bytes, got {len(frame)}")
        self._total_ms += FRAME_MS
        speech = self._is_speech(frame)

        if not self._started:
            self._preroll.append(frame)
            self._speech_run = self._speech_run + 1 if speech else 0
            if self._speech_run < MIN_SPEECH_FRAMES:
                if self._total_ms >= min(self._no_speech_timeout_ms, self._max_duration_ms):
                    return Decision.END_NO_SPEECH
                return Decision.CONTINUE
            self._started = True
            self._frames.extend(self._preroll)
            self._preroll.clear()
        else:
            self._frames.append(frame)

        self._trailing_ms = 0 if speech else self._trailing_ms + FRAME_MS
        if self._trailing_ms >= self._silence_end_ms:
            return Decision.END_SILENCE
        if self._total_ms >= self._max_duration_ms:
            return Decision.END_MAX_DURATION
        return Decision.CONTINUE


def rms_level(frame: bytes) -> float:
    """Return the loudness of a PCM int16 frame on a 0..1 log-ish scale.

    -60 dBFS maps to 0 and -10 dBFS or louder maps to 1.
    """
    samples = np.frombuffer(frame, dtype="<i2").astype(np.float64) / 32768.0
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    if rms <= 0.0:
        return 0.0
    decibels = 20.0 * math.log10(rms)
    return min(1.0, max(0.0, (decibels + 60.0) / 50.0))
