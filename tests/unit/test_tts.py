"""Tests for PiperSpeaker and NullSpeaker, using fake sherpa voices and players (no audio device)."""

from __future__ import annotations

import logging
import sys
import threading
import time
import types
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voicepilot.tts.piper import NullSpeaker, PiperSpeaker, _load_sherpa_voice, _split_sentences, _to_pcm

RATE = 22050


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def pcm_of(word: str) -> bytes:
    """The int16 PCM that FakeVoice produces for one word: one sample per character code."""
    return np.array([ord(c) for c in word], dtype="<i2").tobytes()


class FakeVoice:
    """Mimics sherpa_onnx.OfflineTts: generate() calls the callback once per word.

    The float samples encode the word's character codes, so tests can check what was played.
    A zero callback return stops generation, as in sherpa-onnx; 1 continues.
    """

    sample_rate = RATE

    def __init__(self) -> None:
        self.generated: list[str] = []

    def generate(
        self, text: str, sid: int = 0, speed: float = 1.0, callback: Callable[[Any, float], int] | None = None
    ) -> SimpleNamespace:
        self.generated.append(text)
        for word in text.split():
            samples = np.array([ord(c) for c in word], dtype=np.float32) / 32767
            if callback is not None and callback(samples, 0.5) == 0:
                break
        return SimpleNamespace(samples=[], sample_rate=RATE)


class RecordingPlayer:
    """Records every block played; can block until stop is requested, like a real device."""

    def __init__(self, block_on: str | None = None) -> None:
        self.played: list[bytes] = []
        self.started = threading.Event()
        self.returned_at: float | None = None
        self._block_on = block_on
        self._lock = threading.Lock()

    def __call__(self, pcm: bytes, rate: int, stop_event: threading.Event) -> None:
        assert rate == RATE
        with self._lock:
            self.played.append(pcm)
        self.started.set()
        if self._block_on is not None and pcm == pcm_of(self._block_on):
            stop_event.wait(5.0)
            self.returned_at = time.monotonic()


@pytest.fixture
def model(tmp_path: Path) -> Path:
    path = tmp_path / "vi_VN-vais1000-medium.onnx"
    path.write_bytes(b"not really a model")
    return path


def test_speak_synthesizes_and_plays_each_chunk(model: Path) -> None:
    player = RecordingPlayer()
    speaker = PiperSpeaker(model, player=player, voice_loader=lambda _: FakeVoice())
    speaker.speak("đã lưu")
    assert wait_until(lambda: len(player.played) == 2)
    assert player.played == [pcm_of("đã"), pcm_of("lưu")]


def test_each_sentence_is_generated_by_its_own_call(model: Path) -> None:
    voice = FakeVoice()
    player = RecordingPlayer()
    speaker = PiperSpeaker(model, player=player, voice_loader=lambda _: voice)
    speaker.speak("Xin chào. Hôm nay đẹp trời! Bạn khỏe không?")
    assert wait_until(lambda: len(player.played) == 9)
    assert voice.generated == ["Xin chào.", "Hôm nay đẹp trời!", "Bạn khỏe không?"]


def test_sentence_split_keeps_commas_and_drops_blanks() -> None:
    assert _split_sentences("  Một, hai; ba.  Bốn?  ") == ["Một, hai; ba.", "Bốn?"]
    assert _split_sentences("   ") == []


def test_float_samples_become_int16_pcm() -> None:
    pcm = _to_pcm(np.array([0.0, 1.0, -1.0, 2.0, -2.0, 0.5], dtype=np.float32))
    assert np.frombuffer(pcm, dtype="<i2").tolist() == [0, 32767, -32767, 32767, -32767, 16384]


def test_disabled_speaker_does_nothing(model: Path) -> None:
    loads: list[Path] = []
    player = RecordingPlayer()
    speaker = PiperSpeaker(model, enabled=False, player=player,
                           voice_loader=lambda p: loads.append(p) or FakeVoice())
    speaker.speak("xin chào")
    speaker.stop()
    time.sleep(0.05)
    assert loads == []
    assert player.played == []


def test_missing_model_warns_once_and_becomes_noop(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    player = RecordingPlayer()
    speaker = PiperSpeaker(tmp_path / "missing.onnx", player=player)
    with caplog.at_level(logging.WARNING, logger="voicepilot.tts.piper"):
        speaker.speak("một")
        assert wait_until(lambda: any("unavailable" in r.getMessage() for r in caplog.records))
        speaker.speak("hai")
        time.sleep(0.05)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert player.played == []


def test_voice_loads_once_across_utterances(model: Path) -> None:
    loads: list[Path] = []
    player = RecordingPlayer()
    speaker = PiperSpeaker(model, player=player, voice_loader=lambda p: loads.append(p) or FakeVoice())
    speaker.speak("một")
    assert wait_until(lambda: len(player.played) == 1)
    speaker.speak("hai")
    assert wait_until(lambda: len(player.played) == 2)
    assert len(loads) == 1


def test_stop_interrupts_playback_within_100_ms(model: Path) -> None:
    player = RecordingPlayer(block_on="dài")
    speaker = PiperSpeaker(model, player=player, voice_loader=lambda _: FakeVoice())
    speaker.speak("dài dài")
    assert player.started.wait(2.0)
    stop_at = time.monotonic()
    speaker.stop()
    assert wait_until(lambda: player.returned_at is not None)
    assert player.returned_at is not None
    assert player.returned_at - stop_at < 0.1
    time.sleep(0.05)
    assert player.played == [pcm_of("dài")]  # the second sentence was never generated or played


def test_new_speak_stops_previous_utterance(model: Path) -> None:
    player = RecordingPlayer(block_on="cũ")
    speaker = PiperSpeaker(model, player=player, voice_loader=lambda _: FakeVoice())
    speaker.speak("cũ cũ")
    assert player.started.wait(2.0)
    speaker.speak("mới")
    assert wait_until(lambda: pcm_of("mới") in player.played)
    assert pcm_of("cũ") in player.played
    # the old utterance did not continue after being replaced
    assert player.played.count(pcm_of("cũ")) == 1


def test_player_failure_is_contained(model: Path, caplog: pytest.LogCaptureFixture) -> None:
    def broken(pcm: bytes, rate: int, stop_event: threading.Event) -> None:
        raise OSError("no output device")

    speaker = PiperSpeaker(model, player=broken, voice_loader=lambda _: FakeVoice())
    with caplog.at_level(logging.WARNING, logger="voicepilot.tts.piper"):
        speaker.speak("lỗi")
        assert wait_until(lambda: any("Speech output failed" in r.getMessage() for r in caplog.records))


def test_empty_text_is_ignored(model: Path) -> None:
    loads: list[Path] = []
    speaker = PiperSpeaker(
        model, player=RecordingPlayer(), voice_loader=lambda p: loads.append(p) or FakeVoice()
    )
    speaker.speak("   ")
    time.sleep(0.05)
    assert loads == []


def test_loader_builds_sherpa_config_from_sibling_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    class Config:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    def offline_tts(config: Config) -> str:
        calls["config"] = config
        return "voice"

    fake = types.ModuleType("sherpa_onnx")
    fake.OfflineTtsVitsModelConfig = Config  # type: ignore[attr-defined]
    fake.OfflineTtsModelConfig = Config  # type: ignore[attr-defined]
    fake.OfflineTtsConfig = Config  # type: ignore[attr-defined]
    fake.OfflineTts = offline_tts  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    # This test covers config wiring; native path adaptation has its own fake regressions.
    monkeypatch.setattr("voicepilot.tts.piper._native_espeak_data", lambda path: (path, None))

    (tmp_path / "tokens.txt").write_text("a 1\n", encoding="utf-8")
    (tmp_path / "espeak-ng-data").mkdir()
    model = tmp_path / "vi.onnx"
    model.write_bytes(b"x")

    assert _load_sherpa_voice(model) == "voice"
    top = calls["config"]
    assert top.kwargs["max_num_sentences"] == 1
    vits = top.kwargs["model"].kwargs["vits"].kwargs
    assert vits == {"model": str(model), "tokens": str(tmp_path / "tokens.txt"),
                    "data_dir": str(tmp_path / "espeak-ng-data")}


def test_loader_rejects_model_without_tokens_or_espeak_data(model: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _load_sherpa_voice(model)


def test_null_speaker_accepts_calls_silently() -> None:
    speaker = NullSpeaker()
    speaker.speak("bất kỳ")
    speaker.stop()
