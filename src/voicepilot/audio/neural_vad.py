"""Offline Silero speech detection using the existing sherpa-onnx runtime.

Only VAD sees normalized model windows. The recorder retains the original PCM
for recognition, and Endpointer owns the user's silence and duration limits.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from voicepilot.audio.vad import FRAME_BYTES, SAMPLE_RATE, SpeechPredicate, webrtc_is_speech
from voicepilot.paths import models_dir

log = logging.getLogger(__name__)
MODEL_RELATIVE_PATH = Path("vad/silero-v6/silero_vad.onnx")
WINDOW_SHIFT = 512
CONTEXT_SAMPLES = 64
FALLBACK_NOISE_MARGIN_DB = 8.0


def _load_model(path: Path) -> Any:
    import sherpa_onnx  # lazy native import; shares ONNX Runtime with ASR and TTS

    silero = sherpa_onnx.SileroVadModelConfig(
        model=str(path), threshold=0.6, window_size=WINDOW_SHIFT,
        min_speech_duration=0.032, min_silence_duration=0.032,
    )
    config = sherpa_onnx.VadModelConfig(
        silero_vad=silero, sample_rate=SAMPLE_RATE, num_threads=1, provider="cpu",
    )
    model = sherpa_onnx.VadModel.create(config)
    if model.window_size() != WINDOW_SHIFT + CONTEXT_SAMPLES:
        raise ValueError("Expected the bundled Silero model window")
    return model


class SileroSpeechDetector:
    """Adapt 30 ms PCM frames to contiguous 32 ms windows with 64 samples of context.

    The native model has recurrent state; reset it between turns, including after
    cancellation. Each Recorder owns one instance; Controller serializes recording.
    """

    def __init__(self, path: Path, *, model_loader: Callable[[Path], Any] = _load_model) -> None:
        self._model = model_loader(path)
        self.reset()

    def reset(self) -> None:
        self._model.reset()
        self._pending = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
        self._speech = False

    def __call__(self, frame: bytes) -> bool:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"frame must be {FRAME_BYTES} bytes, got {len(frame)}")
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        self._pending = np.concatenate((self._pending, samples))
        window = WINDOW_SHIFT + CONTEXT_SAMPLES
        while len(self._pending) >= window:
            self._speech = bool(self._model.is_speech(self._pending[:window]))
            self._pending = self._pending[WINDOW_SHIFT:]
        return self._speech


def load_speech_detector() -> tuple[SpeechPredicate, float | None]:
    """Prefer bundled Silero, retaining WebRTC for existing model-less checkouts."""
    path = models_dir() / MODEL_RELATIVE_PATH
    try:
        if not path.is_file():
            raise FileNotFoundError("Silero model missing")
        detector = SileroSpeechDetector(path)
    except Exception as exc:
        # Ordinary logs never include paths or captured content.
        log.warning("Neural VAD unavailable; using WebRTC: %s", type(exc).__name__)
        return webrtc_is_speech(), FALLBACK_NOISE_MARGIN_DB
    return detector, None
