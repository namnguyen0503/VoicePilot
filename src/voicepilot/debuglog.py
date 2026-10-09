"""Opt-in diagnostic log for `--debug` runs: transcripts, parsed commands and the audio of each turn.

The normal log never holds speech. This logger is separate: it does not propagate to the root
logger and has no handler until enable() is called, so without --debug nothing is recorded.
With --debug it writes logs/debug.log and logs/clips/turn-<time>-<id>.wav.
"""

from __future__ import annotations

import logging
import time
import wave
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEBUG_LOG_FILE = "debug.log"
CLIPS_DIR = "clips"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 2

debug_log = logging.getLogger("voicepilot.debug")
debug_log.propagate = False
debug_log.setLevel(logging.CRITICAL + 1)

_clips_dir: Path | None = None


def enable(log_dir: Path) -> None:
    """Start writing the diagnostic log and turn clips under log_dir."""
    global _clips_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / DEBUG_LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    debug_log.addHandler(handler)
    debug_log.setLevel(logging.DEBUG)
    _clips_dir = log_dir / CLIPS_DIR


def enabled() -> bool:
    """True after enable()."""
    return _clips_dir is not None


def save_clip(turn_id: int, pcm: bytes, sample_rate: int) -> None:
    """Save one turn's mono int16 audio as a WAV file when debugging is on. Errors are logged, not raised."""
    if _clips_dir is None:
        return
    path = _clips_dir / f"turn-{time.strftime('%Y%m%d-%H%M%S')}-{turn_id}.wav"
    try:
        _clips_dir.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(sample_rate)
            out.writeframes(pcm)
        debug_log.info("turn id=%d clip=%s", turn_id, path.name)
    except OSError as exc:
        debug_log.info("turn id=%d clip not saved: %s", turn_id, type(exc).__name__)


def _reset_for_tests() -> None:
    global _clips_dir
    for handler in list(debug_log.handlers):
        debug_log.removeHandler(handler)
        handler.close()
    debug_log.setLevel(logging.CRITICAL + 1)
    _clips_dir = None
