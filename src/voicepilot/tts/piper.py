"""Offline Vietnamese TTS (Piper VITS voice vi_VN vais1000 medium) run by sherpa-onnx; implements Speaker.

Synthesis uses sherpa_onnx.OfflineTts, which ships its own onnxruntime, so the process
loads a single onnxruntime. Playback goes through sounddevice.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voicepilot.windows.native_paths import short_windows_path as _short_windows_path

log = logging.getLogger(__name__)

PLAY_BLOCK_S = 0.02  # playback is written in blocks this long, so stop() takes effect quickly
INT16_SCALE = 32767
SPEAKER_ID = 0
SPEED = 1.0
NUM_THREADS = 1
TOKENS_NAME = "tokens.txt"  # sits beside the .onnx file, as does espeak-ng-data/
ESPEAK_DATA_NAME = "espeak-ng-data"

SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

PcmPlayer = Callable[[bytes, int, threading.Event], None]


class _CachedVoice:
    """Keep the fallback phoneme directory alive for lazy espeak reads during synthesis."""

    def __init__(self, voice: Any, cache: tempfile.TemporaryDirectory) -> None:
        self._voice = voice
        self._cache = cache

    def __getattr__(self, name: str) -> Any:
        return getattr(self._voice, name)


def _native_espeak_data(data_dir: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    # espeak uses narrow Windows file APIs and can call exit(-1) on an unreadable data path.
    if sys.platform != "win32" or str(data_dir).isascii():
        return data_dir, None
    try:
        short = _short_windows_path(data_dir)
        if str(short).isascii():
            return short, None
    except OSError:
        pass
    # A volume may disable 8.3 names. Copy only phoneme data, not the model weights,
    # into a private temporary directory with an ASCII path.
    system_root = os.environ.get("SystemRoot")
    if not system_root or not system_root.isascii():
        raise OSError("No ASCII Windows temporary directory is available for espeak")
    cache = tempfile.TemporaryDirectory(prefix="voicepilot-espeak-", dir=Path(system_root) / "Temp")
    try:
        target = Path(cache.name) / ESPEAK_DATA_NAME
        shutil.copytree(data_dir, target)
    except Exception:
        cache.cleanup()
        raise
    return target, cache


def _load_sherpa_voice(model_path: Path) -> Any:
    """Load a Piper VITS voice through sherpa-onnx (imported lazily).

    tokens.txt and espeak-ng-data/ must be in the same folder as the .onnx file.
    """
    import sherpa_onnx  # imported only when speech output is used

    model_dir = model_path.parent
    tokens = model_dir / TOKENS_NAME
    data_dir = model_dir / ESPEAK_DATA_NAME
    if not tokens.is_file() or not data_dir.is_dir():
        raise FileNotFoundError(f"{TOKENS_NAME} or {ESPEAK_DATA_NAME} missing beside the model")
    native_data, cache = _native_espeak_data(data_dir)
    try:
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(model_path), tokens=str(tokens), data_dir=str(native_data)
        )
        model = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=NUM_THREADS, provider="cpu")
        voice = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model, max_num_sentences=1))
    except Exception:
        if cache is not None:
            cache.cleanup()
        raise
    return _CachedVoice(voice, cache) if cache is not None else voice


def _split_sentences(text: str) -> list[str]:
    """Split text at sentence-ending punctuation.

    Each sentence is synthesized separately so cancellation can be checked before
    starting another sentence, in addition to the native generation callback.
    """
    parts = (part.strip() for part in SENTENCE_END_RE.split(text.strip()))
    return [part for part in parts if part]


def _to_pcm(samples: Any) -> bytes:
    """Convert float32 samples in [-1, 1] to mono int16 little-endian PCM bytes."""
    import numpy as np  # numpy is a runtime dependency; imported here to keep module import light

    scaled = np.rint(np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * INT16_SCALE)
    return scaled.astype("<i2").tobytes()


def _play_pcm(pcm: bytes, sample_rate: int, stop_event: threading.Event) -> None:
    """Play mono int16 PCM through the default output device until done or stop_event is set."""
    import sounddevice as sd  # imported lazily, see _load_sherpa_voice

    block_bytes = max(2, int(sample_rate * PLAY_BLOCK_S) * 2)
    with sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16", latency="low") as out:
        for offset in range(0, len(pcm), block_bytes):
            if stop_event.is_set():
                return
            out.write(pcm[offset : offset + block_bytes])


class PiperSpeaker:
    """Speaks text with a local Piper voice run by sherpa-onnx.

    speak() returns at once; synthesis and playback run on a background thread.
    Audio is synthesized sentence by sentence. stop() or a new speak() cancels the utterance:
    playback stops within one block, and no further sentence is generated. The voice loads once,
    on first use. A missing or unusable model is logged once, and speak() then does nothing.
    """

    def __init__(
        self,
        model_path: Path,
        enabled: bool = True,
        player: PcmPlayer | None = None,
        *,
        voice_loader: Callable[[Path], Any] | None = None,
    ) -> None:
        """Create the speaker.

        Args:
            model_path: Path to the Piper ``.onnx`` voice. ``tokens.txt`` and ``espeak-ng-data/``
                must sit in the same folder.
            enabled: When False, speak() and stop() do nothing.
            player: Plays one PCM block; must return soon after stop_event is set.
                Defaults to sounddevice playback.
            voice_loader: Test seam for loading a voice. Defaults to sherpa-onnx OfflineTts.
        """
        self._model_path = model_path
        self._enabled = enabled
        self._player = player if player is not None else _play_pcm
        self._load_voice = voice_loader if voice_loader is not None else _load_sherpa_voice
        self._voice: Any = None
        self._voice_unavailable = False
        self._voice_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._current: threading.Event | None = None

    def speak(self, text: str) -> None:
        """Start speaking text in the background, replacing any utterance in progress."""
        if not self._enabled or not text.strip():
            return
        cancel = threading.Event()
        with self._state_lock:
            if self._current is not None:
                self._current.set()
            self._current = cancel
        threading.Thread(
            target=self._run, args=(text, cancel), name="voicepilot-tts", daemon=True
        ).start()

    def stop(self) -> None:
        """Interrupt the utterance in progress."""
        with self._state_lock:
            if self._current is not None:
                self._current.set()
                self._current = None

    def _run(self, text: str, cancel: threading.Event) -> None:
        try:
            voice = self._get_voice()
            if voice is None or cancel.is_set():
                return
            rate = int(voice.sample_rate)

            def on_chunk(samples: Any, _progress: float) -> int:
                # sherpa-onnx requires 1 to continue generation and 0 to stop.
                if cancel.is_set():
                    return 0
                self._player(_to_pcm(samples), rate, cancel)
                return 0 if cancel.is_set() else 1

            for sentence in _split_sentences(text):
                if cancel.is_set():
                    return
                voice.generate(sentence, sid=SPEAKER_ID, speed=SPEED, callback=on_chunk)
        except Exception as exc:
            log.warning("Speech output failed: %s", type(exc).__name__)

    def _get_voice(self) -> Any:
        with self._voice_lock:
            if self._voice is None and not self._voice_unavailable:
                try:
                    if not self._model_path.is_file():
                        raise FileNotFoundError(self._model_path.name)
                    self._voice = self._load_voice(self._model_path)
                except Exception as exc:
                    self._voice_unavailable = True
                    log.warning("Speech voice unavailable, speech output disabled: %s", type(exc).__name__)
            return self._voice


class NullSpeaker:
    """Speaker that says nothing. Used when speech output is off or unavailable."""

    def speak(self, text: str) -> None:
        """Ignore the text."""

    def stop(self) -> None:
        """Nothing to stop."""
