"""Windows regression: espeak aborts the process on a Unicode data directory."""

import gc
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

from voicepilot.tts import piper


def fake_sherpa(monkeypatch):
    captured = {}
    def vits(**kwargs):
        captured.update(kwargs)
        # Reproduce espeak's narrow-path precondition without invoking native exit(-1).
        assert kwargs["data_dir"].isascii(), "espeak cannot open Unicode paths on Windows"
        return kwargs
    class Voice:
        def __init__(self, config):
            self.data_dir = Path(captured["data_dir"])
        def generate(self, *args):
            return (self.data_dir / "phontab").read_bytes()
    module = types.SimpleNamespace(
        OfflineTtsVitsModelConfig=vits,
        OfflineTtsModelConfig=lambda **kwargs: kwargs,
        OfflineTtsConfig=lambda **kwargs: kwargs,
        OfflineTts=Voice,
    )
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    monkeypatch.setattr(sys, "platform", "win32")
    return captured


def fixture_voice(tmp_path):
    folder = tmp_path / "kiểm chứng" / "voice"
    data = folder / "espeak-ng-data"
    data.mkdir(parents=True)
    (data / "phontab").write_bytes(b"synthetic phonemes")
    (folder / "tokens.txt").write_text("synthetic", encoding="utf-8")
    return folder / "voice.onnx"


def test_tts_uses_ascii_short_path_for_unicode_directory(monkeypatch, tmp_path):
    captured = fake_sherpa(monkeypatch)
    model = fixture_voice(tmp_path)
    monkeypatch.setattr(piper, "_short_windows_path", lambda _: Path("C:/SYNTHETIC/data"), raising=False)
    piper._load_sherpa_voice(model)
    assert captured["data_dir"] == str(Path("C:/SYNTHETIC/data"))


def test_tts_without_short_names_retains_ascii_cache_for_voice_lifetime(monkeypatch, tmp_path):
    fake_sherpa(monkeypatch)
    model = fixture_voice(tmp_path)
    monkeypatch.setattr(piper, "_short_windows_path", lambda path: path, raising=False)
    # The repo itself may have a Unicode path, so this fake Windows root must be ASCII.
    base = Path(os.environ["SystemRoot"]) / "Temp" if os.name == "nt" else None
    with tempfile.TemporaryDirectory(prefix="voicepilot-path-test-", dir=base) as system_root:
        monkeypatch.setenv("SystemRoot", system_root)
        (Path(system_root) / "Temp").mkdir()
        voice = piper._load_sherpa_voice(model)
        cached = voice.data_dir
        assert voice.generate("synthetic") == b"synthetic phonemes"
        assert cached.exists() and cached != model.parent / "espeak-ng-data"
        del voice
        gc.collect()
        assert not cached.exists()


def test_no_ascii_directory_fails_before_calling_native_constructor(monkeypatch, tmp_path):
    captured = fake_sherpa(monkeypatch)
    model = fixture_voice(tmp_path)
    monkeypatch.setattr(piper, "_short_windows_path", lambda path: path)
    monkeypatch.delenv("SystemRoot", raising=False)
    with pytest.raises(OSError, match="No ASCII"):
        piper._load_sherpa_voice(model)
    assert not captured
