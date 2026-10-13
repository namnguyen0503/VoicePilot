"""Unit tests for contracts (dataclass behavior) and config (load, merge, save)."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from voicepilot.config import AppConfig, load_config, save_config
from voicepilot.contracts import (
    AudioClip,
    Command,
    Intent,
    SttBackendMode,
)


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point VOICEPILOT_CONFIG_DIR at an empty temporary directory."""
    home = tmp_path / "VoicePilot"
    monkeypatch.setenv("VOICEPILOT_CONFIG_DIR", str(home))
    return home


def test_command_is_executable_requires_known_intent_and_no_missing_params() -> None:
    assert Command(Intent.PPT_NEXT_SLIDE, "slide tiếp").is_executable
    assert not Command(Intent.UNKNOWN, "blah").is_executable
    assert not Command(Intent.BROWSER_SEARCH, "tìm", missing=("query",)).is_executable


def test_command_params_default_to_independent_dicts() -> None:
    first = Command(Intent.WIN_OPEN_APP, "mở word")
    second = Command(Intent.WIN_OPEN_APP, "mở excel")
    first.params["app"] = "word"
    assert second.params == {}


def test_audio_clip_duration_uses_int16_mono_samples() -> None:
    clip = AudioClip(samples=b"\x00\x00" * 8000, turn_id=1)
    assert clip.sample_rate == 16000
    assert clip.duration_s == pytest.approx(0.5)


def test_load_config_returns_bundled_defaults(config_home: Path) -> None:
    cfg = load_config()
    assert cfg.hotkey_listen == "ctrl+alt+v"
    assert cfg.hotkey_mode == "ctrl+alt+m"
    assert cfg.silence_end_ms == 800
    assert cfg.max_command_s == 15
    assert cfg.max_dictation_s == 30
    assert cfg.stt_mode is SttBackendMode.HYBRID
    assert cfg.overlay_pos is None
    assert cfg.website_aliases["youtube"] == "https://www.youtube.com"
    assert cfg.website_aliases["du túp"] == "https://www.youtube.com"
    assert cfg.app_aliases["powerpoint"] == "POWERPNT.EXE"
    assert cfg.app_aliases["máy tính"] == "calc.exe"


def test_user_override_merges_with_defaults(config_home: Path) -> None:
    config_home.mkdir(parents=True)
    (config_home / "config.toml").write_text(
        "silence_end_ms = 1200\n"
        'stt_mode = "offline"\n'
        "overlay_pos = [100, 200]\n"
        "[websites]\n"
        '"ghi chú" = "https://example.com/notes"\n'
        "[apps]\n"
        '"zoom" = "Zoom.exe"\n',
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.silence_end_ms == 1200
    assert cfg.stt_mode is SttBackendMode.OFFLINE
    assert cfg.overlay_pos == (100, 200)
    # Alias tables merge key by key: defaults survive, user keys are added.
    assert cfg.website_aliases["ghi chú"] == "https://example.com/notes"
    assert cfg.website_aliases["youtube"] == "https://www.youtube.com"
    assert cfg.app_aliases["zoom"] == "Zoom.exe"
    assert cfg.app_aliases["notepad"] == "notepad.exe"
    # Untouched keys keep their default values.
    assert cfg.tts_voice == "vi_VN-vais1000-medium"


def test_corrupt_user_config_falls_back_to_defaults(
    config_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config_home.mkdir(parents=True)
    (config_home / "config.toml").write_text("silence_end_ms = [unclosed\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="voicepilot.config"):
        cfg = load_config()
    assert cfg.silence_end_ms == 800
    assert cfg.website_aliases["youtube"] == "https://www.youtube.com"
    assert any("config" in rec.getMessage().lower() for rec in caplog.records)


def test_wrongly_typed_user_value_falls_back_to_defaults(config_home: Path) -> None:
    config_home.mkdir(parents=True)
    (config_home / "config.toml").write_text('silence_end_ms = "fast"\n', encoding="utf-8")
    assert load_config().silence_end_ms == 800


def test_save_and_load_round_trip(config_home: Path) -> None:
    cfg = load_config()
    cfg.silence_end_ms = 950
    cfg.stt_mode = SttBackendMode.GOOGLE
    cfg.tts_enabled = False
    cfg.overlay_pos = (640, 1000)
    cfg.app_aliases["zoom"] = "Zoom.exe"

    save_config(cfg)
    assert (config_home / "config.toml").is_file()

    loaded = load_config()
    assert loaded == cfg
    assert isinstance(loaded, AppConfig)


def test_save_omits_unset_overlay_position(config_home: Path) -> None:
    save_config(AppConfig(), config_home / "custom.toml")
    text = (config_home / "custom.toml").read_text(encoding="utf-8")
    assert "overlay_pos" not in text
    assert load_config(config_home / "custom.toml").overlay_pos is None


def _write_user_file(home: Path, data: bytes) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_bytes(data)
    return path


def test_one_wrongly_typed_key_keeps_the_other_settings(config_home: Path) -> None:
    path = _write_user_file(
        config_home,
        'silence_end_ms = "800"\nhistory_size = 3\n[websites]\n"ghi chú" = "https://example.org"\n'.encode(),
    )
    cfg = load_config(path)
    assert cfg.silence_end_ms == AppConfig().silence_end_ms
    assert cfg.history_size == 3
    assert cfg.website_aliases["ghi chú"] == "https://example.org"


@pytest.mark.parametrize(
    "line",
    [
        "silence_end_ms = 199",
        "silence_end_ms = 5001",
        "max_command_s = 2",
        "max_dictation_s = 121",
        "no_speech_timeout_s = 0",
        "google_timeout_s = 0.4",
        "google_timeout_s = 10.5",
        "gipformer_threads = 9",
        "history_size = 0",
        "history_size = -1",
        'hotkey_listen = "  "',
        'hotkey_mode = ""',
    ],
)
def test_out_of_range_value_uses_default_and_is_named_in_the_warning(
    config_home: Path, caplog: pytest.LogCaptureFixture, line: str
) -> None:
    key = line.split(" = ")[0]
    path = _write_user_file(config_home, (line + "\n").encode())
    with caplog.at_level(logging.WARNING, logger="voicepilot.config"):
        cfg = load_config(path)
    assert getattr(cfg, key) == getattr(AppConfig(), key)
    assert key in caplog.text


@pytest.mark.parametrize(
    "line",
    [
        "silence_end_ms = 200",
        "silence_end_ms = 5000",
        "history_size = 200",
        "google_timeout_s = 10",
        "gipformer_threads = 8",
    ],
)
def test_range_boundaries_are_accepted(config_home: Path, line: str) -> None:
    key, value = line.split(" = ")
    cfg = load_config(_write_user_file(config_home, (line + "\n").encode()))
    expected = float(value) if key == "google_timeout_s" else int(value)
    assert getattr(cfg, key) == expected


def test_warning_never_contains_the_offending_value(
    config_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write_user_file(config_home, b'history_size = "SECRET-VALUE-42"\n')
    with caplog.at_level(logging.WARNING, logger="voicepilot.config"):
        load_config(path)
    assert "history_size" in caplog.text
    assert "SECRET-VALUE-42" not in caplog.text


def test_utf8_file_with_byte_order_mark_is_honoured(config_home: Path) -> None:
    path = _write_user_file(config_home, b"\xef\xbb\xbfhistory_size = 3\n")
    assert load_config(path).history_size == 3


def test_utf16_file_falls_back_to_defaults(config_home: Path) -> None:
    path = _write_user_file(config_home, 'history_size = 3\n[websites]\n"ghi chú" = "x"\n'.encode("utf-16"))
    cfg = load_config(path)
    assert cfg == load_config(config_home / "missing.toml")


def test_non_text_alias_entries_are_skipped_and_defaults_stay(config_home: Path) -> None:
    path = _write_user_file(config_home, b'[websites]\nbad = 12\n"ghi ch\xc3\xba" = "https://example.org"\n')
    sites = load_config(path).website_aliases
    assert "bad" not in sites
    assert sites["ghi chú"] == "https://example.org"
    assert sites["youtube"] == "https://www.youtube.com"


def test_alias_table_that_is_not_a_table_keeps_default_entries(config_home: Path) -> None:
    path = _write_user_file(config_home, b'websites = "youtube"\n')
    assert load_config(path).website_aliases["youtube"] == "https://www.youtube.com"


def test_fractional_overlay_position_is_ignored(config_home: Path) -> None:
    path = _write_user_file(config_home, b"overlay_pos = [1.5, 2]\n")
    assert load_config(path).overlay_pos is None


def test_start_with_windows_defaults_to_true(config_home: Path) -> None:
    assert AppConfig().start_with_windows is True
    assert load_config().start_with_windows is True


def test_start_with_windows_round_trips_through_config_file(config_home: Path) -> None:
    save_config(replace(AppConfig(), start_with_windows=False))

    assert load_config().start_with_windows is False
    assert "start_with_windows = false" in (config_home / "config.toml").read_text(encoding="utf-8")


def test_wrongly_typed_start_with_windows_falls_back_to_true(config_home: Path) -> None:
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / "config.toml").write_text('start_with_windows = "no"\n', encoding="utf-8")

    assert load_config().start_with_windows is True
