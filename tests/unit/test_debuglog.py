"""The --debug diagnostic log: off by default, separate from the normal log when on."""

from __future__ import annotations

import io
import logging
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

from voicepilot import debuglog


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    debuglog._reset_for_tests()
    yield
    debuglog._reset_for_tests()


def test_disabled_by_default_records_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    debuglog.debug_log.info("heard=%r", "bí mật")
    debuglog.save_clip(1, b"\x00\x00" * 160, 16000)

    assert not debuglog.enabled()
    assert "bí mật" not in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_enabled_writes_text_and_clip_but_not_to_the_normal_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    normal = io.StringIO()
    root_handler = logging.StreamHandler(normal)
    root = logging.getLogger()
    root.addHandler(root_handler)
    monkeypatch.setattr(root, "level", logging.DEBUG)
    try:
        debuglog.enable(tmp_path)
        debuglog.debug_log.info("heard=%r", "mở notepad")
        debuglog.save_clip(7, b"\x01\x00" * 160, 16000)
        for handler in debuglog.debug_log.handlers:
            handler.flush()
    finally:
        root.removeHandler(root_handler)

    assert "mở notepad" in (tmp_path / "debug.log").read_text(encoding="utf-8")
    assert "mở notepad" not in normal.getvalue()
    (clip,) = (tmp_path / "clips").iterdir()
    assert clip.name.endswith("-7.wav")
    with wave.open(str(clip)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getnframes()) == (16000, 1, 160)
