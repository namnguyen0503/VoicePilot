"""Offline Gipformer 1.5 INT8 backend via sherpa-onnx (CPU, greedy) implementing SttBackend."""

from __future__ import annotations

import logging
import threading
import time
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from voicepilot import paths
from voicepilot.contracts import AudioClip, SttError, Transcript

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FEATURE_DIM = 80
DECODING_METHOD = "greedy_search"
MODEL_RESOURCE = "models.toml"
MODEL_ENGINE = "sherpa-onnx"
MODEL_KIND = "asr"
DEFAULT_FILES = {
    "encoder": "encoder.int8.onnx",
    "decoder": "decoder.int8.onnx",
    "joiner": "joiner.int8.onnx",
    "tokens": "tokens.txt",
}
DEFAULT_MODEL_SUBDIR = "gipformer1.5-int8"


def _model_file_names() -> dict[str, str]:
    """Return the file name for each role, read from models.toml with defaults for gaps.

    A role is matched either by an explicit key (encoder = "...") or by the name
    of an entry in the model's ``files`` list that contains the role word.
    """
    names = dict(DEFAULT_FILES)
    try:
        text = (resources.files("voicepilot") / "resources" / MODEL_RESOURCE).read_text(encoding="utf-8")
        manifest = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("Cannot read %s, using default Gipformer file names: %s", MODEL_RESOURCE, exc)
        return names
    for entry in manifest.get("model", []):
        if entry.get("kind") != MODEL_KIND or entry.get("engine") != MODEL_ENGINE:
            continue
        for role in DEFAULT_FILES:
            if isinstance(entry.get(role), str):
                names[role] = entry[role]
        for item in entry.get("files", []):
            file_name = str(item.get("name", ""))
            for role in DEFAULT_FILES:
                if role in file_name:
                    names[role] = file_name
        break
    return names


class GipformerStt:
    """Recognizes Vietnamese offline with a sherpa-onnx transducer model.

    The model loads on first use (or via warmup()) and is shared by all calls.
    Decoding is serialized because a single recognizer is not used concurrently.
    """

    name = "gipformer"

    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        """Create the backend without loading the model.

        Args:
            model_dir: Folder holding the model files. A relative path is resolved
                under ``paths.models_dir() / "asr"``.
            num_threads: CPU threads for sherpa-onnx.
        """
        self._model_dir = model_dir if model_dir.is_absolute() else paths.models_dir() / "asr" / model_dir
        self._num_threads = num_threads
        self._load_lock = threading.Lock()
        self._decode_lock = threading.Lock()
        self._recognizer: Any = None

    def warmup(self) -> None:
        """Load the model now so the first recognition does not pay the load time.

        Raises:
            SttError: When files are missing or the model cannot be loaded.
        """
        self._ensure_loaded()

    def transcribe(self, clip: AudioClip) -> Transcript:
        """Recognize one clip.

        Raises:
            SttError: When the model is unavailable, decoding fails or the text is empty.
        """
        recognizer = self._ensure_loaded()
        start = time.perf_counter()
        samples = np.frombuffer(clip.samples, dtype="<i2").astype(np.float32) / 32768.0
        try:
            with self._decode_lock:
                stream = recognizer.create_stream()
                stream.accept_waveform(clip.sample_rate, samples)
                recognizer.decode_stream(stream)
                raw_text = stream.result.text
        except Exception as exc:
            raise SttError("Gipformer lỗi khi nhận dạng.") from exc

        text = " ".join(str(raw_text).split())
        latency_ms = (time.perf_counter() - start) * 1000.0
        if not text:
            raise SttError("Gipformer trả về kết quả rỗng.")
        return Transcript(text=text, source="gipformer", latency_ms=latency_ms, turn_id=clip.turn_id)

    def _ensure_loaded(self) -> Any:
        with self._load_lock:
            if self._recognizer is None:
                self._recognizer = self._load()
            return self._recognizer

    def _load(self) -> Any:
        names = _model_file_names()
        paths_by_role = {role: self._model_dir / names[role] for role in DEFAULT_FILES}
        missing = [str(p) for p in paths_by_role.values() if not p.is_file()]
        if missing:
            raise SttError("Thiếu tệp mô hình Gipformer: " + ", ".join(missing))
        try:
            import sherpa_onnx  # native dependency, imported only when the model is needed

            return sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(paths_by_role["encoder"]),
                decoder=str(paths_by_role["decoder"]),
                joiner=str(paths_by_role["joiner"]),
                tokens=str(paths_by_role["tokens"]),
                num_threads=self._num_threads,
                sample_rate=SAMPLE_RATE,
                feature_dim=FEATURE_DIM,
                decoding_method=DECODING_METHOD,
                provider="cpu",
            )
        except Exception as exc:
            raise SttError("Không tải được mô hình Gipformer.") from exc
