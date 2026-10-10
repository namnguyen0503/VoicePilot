"""Regression for the real Windows BPE loader's fatal Unicode filename failure."""

import gc
import os
import sys
import tempfile
from pathlib import Path

from voicepilot.stt import gipformer


def test_unicode_hotword_options_reach_native_loader_as_ascii(monkeypatch, tmp_path):
    folder = tmp_path / "cấu hình"
    folder.mkdir()
    hotwords, vocab = folder / "hotwords.txt", folder / "bpe.vocab"
    hotwords.write_text("SYNTHETIC", encoding="utf-8")
    vocab.write_text("SYNTHETIC", encoding="utf-8")
    monkeypatch.setattr(gipformer, "write_hotword_files", lambda *args: (hotwords, vocab))
    monkeypatch.setattr(gipformer, "_native_hotword_files",
                        lambda *args: (Path("C:/SYNTHETIC/hotwords.txt"), Path("C:/SYNTHETIC/bpe.vocab")),
                        raising=False)
    backend = gipformer.GipformerStt(tmp_path, hotwords=["synthetic"])
    options = backend._hotword_options(tmp_path / "tokens.txt")
    assert options["hotwords_file"].isascii()
    assert options["bpe_vocab"].isascii()


def test_hotword_ascii_cache_preserves_files_until_backend_is_released(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(gipformer, "short_windows_path", lambda path: path)
    folder = tmp_path / "cấu hình"
    folder.mkdir()
    files = folder / "hotwords.txt", folder / "bpe.vocab"
    for file in files:
        file.write_bytes(b"synthetic")
    base = Path(os.environ["SystemRoot"]) / "Temp" if os.name == "nt" else None
    with tempfile.TemporaryDirectory(prefix="voicepilot-path-test-", dir=base) as system_root:
        monkeypatch.setenv("SystemRoot", system_root)
        (Path(system_root) / "Temp").mkdir()
        backend = gipformer.GipformerStt(tmp_path)
        native = gipformer._native_hotword_files(files, backend)
        assert all(str(file).isascii() and file.read_bytes() == b"synthetic" for file in native)
        del backend
        gc.collect()
        assert all(not file.exists() for file in native)


def test_unavailable_ascii_hotword_path_falls_back_before_native_load(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(gipformer, "write_hotword_files", lambda *args: (tmp_path / "a", tmp_path / "b"))
    def failed(*args):
        raise OSError("private alias")
    monkeypatch.setattr(gipformer, "_native_hotword_files", failed)
    assert gipformer.GipformerStt(tmp_path, hotwords=["private alias"])._hotword_options(tmp_path / "t") == {}
    assert "private alias" not in caplog.text
