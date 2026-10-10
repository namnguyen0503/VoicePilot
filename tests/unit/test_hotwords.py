"""Tests for hotword phrases and the Gipformer hotword options, with a fake sherpa_onnx module."""

from __future__ import annotations

import sys
import types
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from voicepilot.stt import gipformer
from voicepilot.stt.gipformer import DEFAULT_FILES, GipformerStt, _model_file_names
from voicepilot.stt.hotwords import command_phrases, write_hotword_files

TOKENS_TEXT = "<blk> 0\n▁CHỤP 1\n▁MÀ 2\nN 3\n"


def test_command_phrases_come_from_commands_table() -> None:
    phrases = command_phrases({}, {})

    assert "chụp màn hình" in phrases
    assert "slide tiếp theo" in phrases
    assert "lưu thành" in phrases
    assert "mở ứng dụng" in phrases


def test_command_phrases_add_alias_keys_with_open_prefix() -> None:
    phrases = command_phrases({"notepad": "notepad.exe"}, {"youtube": "https://www.youtube.com"})

    assert "notepad" in phrases
    assert "mở notepad" in phrases
    assert "youtube" in phrases
    assert "mở youtube" in phrases


def test_command_phrases_are_clean_sorted_and_unique() -> None:
    phrases = command_phrases({"Notepad  Plus": "x.exe", "   ": "empty.exe"}, {})

    assert phrases == sorted(set(phrases))
    assert "" not in phrases
    assert "notepad plus" in phrases
    assert all("{" not in phrase and "}" not in phrase for phrase in phrases)
    assert all(phrase == " ".join(phrase.lower().split()) for phrase in phrases)


def test_command_phrases_normalize_unicode_to_nfc() -> None:
    decomposed_key = unicodedata.normalize("NFD", "Chụp Nhanh")

    phrases = command_phrases({decomposed_key: "x"}, {})

    assert unicodedata.normalize("NFC", "chụp nhanh") in phrases


def test_write_hotword_files_uppercases_phrases(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text(TOKENS_TEXT, encoding="utf-8")

    result = write_hotword_files(["chụp màn hình", "mở notepad"], tokens, tmp_path / "out")

    assert result is not None
    hotwords_path, vocab_path = result
    assert hotwords_path == tmp_path / "out" / "hotwords.txt"
    assert vocab_path == tmp_path / "out" / "bpe.vocab"
    assert hotwords_path.read_bytes() == "CHỤP MÀN HÌNH\nMỞ NOTEPAD\n".encode("utf-8")


def test_write_hotword_files_vocab_scores_follow_line_index(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text(TOKENS_TEXT, encoding="utf-8")

    result = write_hotword_files(["chụp"], tokens, tmp_path)

    assert result is not None
    _, vocab_path = result
    lines = vocab_path.read_bytes().decode("utf-8").split("\n")
    assert lines[:4] == ["<blk>\t0", "▁CHỤP\t-1", "▁MÀ\t-2", "N\t-3"]
    assert b"\r" not in vocab_path.read_bytes()


def test_write_hotword_files_returns_none_without_phrases(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text(TOKENS_TEXT, encoding="utf-8")

    assert write_hotword_files([], tokens, tmp_path / "out") is None
    assert not (tmp_path / "out").exists()


@pytest.fixture
def sherpa_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Install a fake sherpa_onnx module and record the kwargs of each from_transducer call."""
    calls: list[dict[str, Any]] = []
    module = types.ModuleType("sherpa_onnx")

    class OfflineRecognizer:
        @staticmethod
        def from_transducer(**kwargs: Any) -> object:
            calls.append(kwargs)
            return object()

    module.OfflineRecognizer = OfflineRecognizer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    return calls


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A model folder with placeholder files, and a private config directory for the phrase files."""
    folder = tmp_path / "model"
    folder.mkdir()
    names = _model_file_names()
    for role in DEFAULT_FILES:
        (folder / names[role]).write_bytes(b"")
    (folder / names["tokens"]).write_text(TOKENS_TEXT, encoding="utf-8")
    monkeypatch.setenv("VOICEPILOT_CONFIG_DIR", str(tmp_path / "cfg"))
    return folder


def test_load_with_hotwords_uses_modified_beam_search(
    model_dir: Path, sherpa_calls: list[dict[str, Any]], tmp_path: Path
) -> None:
    backend = GipformerStt(model_dir, hotwords=["chụp màn hình"])
    backend.warmup()

    (kwargs,) = sherpa_calls
    assert kwargs["decoding_method"] == "modified_beam_search"
    assert kwargs["modeling_unit"] == "bpe"
    assert kwargs["hotwords_score"] == 4.0
    assert kwargs["max_active_paths"] == gipformer.MAX_ACTIVE_PATHS
    stt_dir = tmp_path / "cfg" / "stt"
    assert Path(kwargs["hotwords_file"]).read_bytes() == (stt_dir / "hotwords.txt").read_bytes()
    assert Path(kwargs["bpe_vocab"]).read_bytes() == (stt_dir / "bpe.vocab").read_bytes()
    assert (stt_dir / "hotwords.txt").read_bytes() == "CHỤP MÀN HÌNH\n".encode("utf-8")


def test_load_without_hotwords_stays_greedy(model_dir: Path, sherpa_calls: list[dict[str, Any]]) -> None:
    GipformerStt(model_dir).warmup()

    (kwargs,) = sherpa_calls
    assert kwargs["decoding_method"] == "greedy_search"
    assert "modeling_unit" not in kwargs
    assert "hotwords_file" not in kwargs


def test_load_falls_back_to_greedy_when_files_cannot_be_written(
    model_dir: Path,
    sherpa_calls: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("VOICEPILOT_CONFIG_DIR", str(blocker))

    with caplog.at_level("WARNING"):
        GipformerStt(model_dir, hotwords=["chụp màn hình"]).warmup()

    (kwargs,) = sherpa_calls
    assert kwargs["decoding_method"] == "greedy_search"
    assert "hotwords_file" not in kwargs
    assert "chụp" not in caplog.text.lower()


def test_load_falls_back_to_greedy_when_hotword_recognizer_fails(
    model_dir: Path, sherpa_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sherpa_onnx

    recorded = sherpa_onnx.OfflineRecognizer.from_transducer

    def reject_hotwords(**kwargs: Any) -> object:
        recorded(**kwargs)
        if "hotwords_file" in kwargs:
            raise RuntimeError("bad hotwords")
        return object()

    monkeypatch.setattr(sherpa_onnx.OfflineRecognizer, "from_transducer", staticmethod(reject_hotwords))

    GipformerStt(model_dir, hotwords=["chụp màn hình"]).warmup()

    assert [call["decoding_method"] for call in sherpa_calls] == ["modified_beam_search", "greedy_search"]
