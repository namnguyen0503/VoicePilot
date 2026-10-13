"""Black-box QA tests for config loading and saving (PLAN.md "Dữ liệu", COMMANDS.vi.md alias tables)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from voicepilot.config import CONFIG_ENV_VAR, AppConfig, config_dir, load_config, save_config
from voicepilot.contracts import SttBackendMode


def write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


def test_shipped_defaults_match_plan(config_home):
    cfg = load_config()
    assert cfg.hotkey_listen == "ctrl+alt+v"  # PLAN §1
    assert cfg.hotkey_mode == "ctrl+alt+m"
    assert cfg.silence_end_ms == 800
    assert cfg.max_command_s == 15 and cfg.max_dictation_s == 30  # PLAN §2 limits
    assert cfg.google_timeout_s == 2.0
    assert cfg.stt_mode is SttBackendMode.HYBRID
    assert cfg.gipformer_threads == 2
    assert cfg.tts_voice == "vi_VN-vais1000-medium"
    assert cfg.history_size == 20  # PLAN: keep the last 20 utterances


def test_shipped_website_table_matches_commands_doc(config_home):
    # COMMANDS.vi.md "Tên website"
    sites = load_config().website_aliases
    expected = {
        "youtube": "https://www.youtube.com",
        "facebook": "https://www.facebook.com",
        "google": "https://www.google.com",
        "gmail": "https://mail.google.com",
        "github": "https://github.com",
        "chatgpt": "https://chatgpt.com",
        "claude": "https://claude.ai",
        # Gipformer hears "claude.ai" as "cloud chấm ai" (real Windows recording).
        "cloud chấm ai": "https://claude.ai",
        "vnexpress": "https://vnexpress.net",
        "zalo": "https://chat.zalo.me",
        "shopee": "https://shopee.vn",
        "wikipedia": "https://vi.wikipedia.org",
    }
    for name, url in expected.items():
        assert sites[name] == url


def test_shipped_app_table_covers_documented_names(config_home):
    # COMMANDS.vi.md "Tên ứng dụng"
    apps = load_config().app_aliases
    for name in ["powerpoint", "word", "excel", "edge", "chrome", "notepad", "máy tính"]:
        assert name in apps


def test_missing_file_gives_defaults(config_home):
    assert load_config() == load_config(config_home / "nope.toml")


def test_user_website_is_merged_and_defaults_survive(config_home):
    path = write(config_home / "config.toml", '[websites]\n"ghi chú" = "https://example.org/notes"\n')
    cfg = load_config(path)
    assert cfg.website_aliases["ghi chú"] == "https://example.org/notes"
    assert cfg.website_aliases["youtube"] == "https://www.youtube.com"


def test_user_can_override_a_default_alias(config_home):
    path = write(config_home / "config.toml", '[websites]\nyoutube = "https://m.youtube.com"\n')
    assert load_config(path).website_aliases["youtube"] == "https://m.youtube.com"


def test_loads_do_not_leak_user_aliases_into_each_other(config_home):
    first = write(config_home / "a.toml", '[websites]\n"riêng" = "https://a.example"\n')
    second = write(config_home / "b.toml", "history_size = 5\n")
    load_config(first)
    assert "riêng" not in load_config(second).website_aliases


def test_corrupt_toml_falls_back_to_defaults(config_home):
    path = write(config_home / "config.toml", "hotkey_listen = \n[websites\n")
    assert load_config(path) == load_config(config_home / "missing.toml")


def test_unknown_keys_are_ignored(config_home):
    path = write(config_home / "config.toml", "future_option = true\nhistory_size = 7\n")
    cfg = load_config(path)
    assert cfg.history_size == 7
    assert not hasattr(cfg, "future_option")


def test_stt_mode_values_are_parsed(config_home):
    path = write(config_home / "config.toml", 'stt_mode = "offline"\n')
    assert load_config(path).stt_mode is SttBackendMode.OFFLINE


def test_unknown_stt_mode_falls_back_to_defaults(config_home):
    path = write(config_home / "config.toml", 'stt_mode = "cloud"\nhistory_size = 3\n')
    assert load_config(path).stt_mode is SttBackendMode.HYBRID


@pytest.mark.parametrize(
    "line", ['overlay_pos = "left"', "overlay_pos = [1]", "overlay_pos = [1, 2, 3]", "overlay_pos = 5"]
)
def test_bad_overlay_position_is_ignored(config_home, line):
    path = write(config_home / "config.toml", line + "\n")
    assert load_config(path).overlay_pos is None


def test_valid_overlay_position_is_a_tuple(config_home):
    path = write(config_home / "config.toml", "overlay_pos = [120, 900]\n")
    assert load_config(path).overlay_pos == (120, 900)


def test_directory_in_place_of_file_gives_defaults(config_home):
    directory = config_home / "config.toml"
    directory.mkdir(parents=True)
    assert load_config(directory) == load_config(config_home / "missing.toml")


def test_bool_is_not_accepted_as_int(config_home):
    path = write(config_home / "config.toml", "history_size = true\n")
    assert load_config(path).history_size == 20


def test_float_timeout_accepts_integer(config_home):
    path = write(config_home / "config.toml", "google_timeout_s = 3\n")
    cfg = load_config(path)
    assert cfg.google_timeout_s == 3.0 and isinstance(cfg.google_timeout_s, float)


def test_one_bad_value_keeps_other_user_settings(config_home):
    path = write(
        config_home / "config.toml",
        'silence_end_ms = "800"\nhistory_size = 3\n[websites]\n"ghi chú" = "https://example.org"\n',
    )
    cfg = load_config(path)
    assert "ghi chú" in cfg.website_aliases
    assert cfg.history_size == 3


def test_non_utf8_config_file_does_not_crash_startup(config_home):
    path = config_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("history_size = 5\n# ghi chú\n".encode("utf-16"))
    assert load_config(path) is not None


def test_utf8_bom_config_is_honoured(config_home):
    path = config_home / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbfhistory_size = 3\n")
    assert load_config(path).history_size == 3


@pytest.mark.parametrize("line", ["history_size = -1", "gipformer_threads = 0", "silence_end_ms = -5"])
def test_numeric_settings_must_be_positive(config_home, line):
    path = write(config_home / "config.toml", line + "\n")
    cfg = load_config(path)
    assert cfg.history_size >= 0 and cfg.gipformer_threads >= 1 and cfg.silence_end_ms >= 0


def test_config_dir_honours_env_with_unicode_and_spaces(tmp_path, monkeypatch):
    target = tmp_path / "Thư mục VoicePilot có dấu"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(target))
    assert config_dir() == target


def test_round_trip_through_unicode_path_with_spaces(tmp_path, monkeypatch):
    home = tmp_path / "Thư mục có khoảng trắng"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(home))
    cfg = load_config()
    cfg.history_size = 9
    cfg.overlay_pos = (640, 1040)
    cfg.stt_mode = SttBackendMode.GOOGLE
    cfg.website_aliases['chat "gpt" 2'] = "https://chatgpt.com/?q=a&b=1"
    save_config(cfg)
    assert (home / "config.toml").is_file()
    again = load_config()
    assert again.history_size == 9
    assert again.overlay_pos == (640, 1040)
    assert again.stt_mode is SttBackendMode.GOOGLE
    assert again.website_aliases['chat "gpt" 2'] == "https://chatgpt.com/?q=a&b=1"
    assert again.website_aliases["youtube"] == "https://www.youtube.com"


def test_save_omits_unset_overlay_position(config_home):
    save_config(AppConfig(), config_home / "config.toml")
    assert "overlay_pos" not in (config_home / "config.toml").read_text(encoding="utf-8")


def test_config_dir_default_when_env_unset(monkeypatch):
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    if os.name == "nt":
        pytest.skip("Windows default is %LOCALAPPDATA%")
    assert config_dir() == Path.home() / ".local" / "share" / "VoicePilot"


def test_empty_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(CONFIG_ENV_VAR, "")
    if os.name == "nt":
        pytest.skip("Windows default is %LOCALAPPDATA%")
    assert config_dir() == Path.home() / ".local" / "share" / "VoicePilot"
