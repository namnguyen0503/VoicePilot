"""Application configuration: defaults from resources, user overrides from config.toml."""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any

import tomli_w

from voicepilot.contracts import SttBackendMode

log = logging.getLogger(__name__)

CONFIG_ENV_VAR = "VOICEPILOT_CONFIG_DIR"
CONFIG_FILE_NAME = "config.toml"
DEFAULTS_RESOURCE = "defaults.toml"
_ALIAS_TABLES = {"websites": "website_aliases", "apps": "app_aliases"}
# Inclusive bounds for numeric settings. A value outside them falls back to its default.
_RANGES: dict[str, tuple[float, float]] = {
    "silence_end_ms": (200, 5000),
    "max_command_s": (3, 60),
    "max_dictation_s": (5, 120),
    "no_speech_timeout_s": (1, 30),
    "google_timeout_s": (0.5, 10),
    "gipformer_threads": (1, 8),
    "history_size": (1, 200),
}
_NON_EMPTY_STRINGS = frozenset({"hotkey_listen", "hotkey_mode"})


@dataclass
class AppConfig:
    """All user-tunable settings. Defaults mirror resources/defaults.toml.

    The alias dicts map a spoken name (lowercase, with Vietnamese diacritics kept)
    to a URL (websites) or a launch target such as "POWERPNT.EXE" (apps).
    """

    hotkey_listen: str = "ctrl+alt+space"
    hotkey_mode: str = "ctrl+alt+d"
    vad_enabled: bool = True
    silence_end_ms: int = 800
    max_command_s: int = 15
    max_dictation_s: int = 30
    no_speech_timeout_s: int = 5
    google_timeout_s: float = 2.0
    stt_mode: SttBackendMode = SttBackendMode.HYBRID
    gipformer_threads: int = 2
    tts_enabled: bool = True
    tts_voice: str = "vi_VN-vais1000-medium"
    history_size: int = 20
    overlay_pos: tuple[int, int] | None = None
    website_aliases: dict[str, str] = field(default_factory=dict)
    app_aliases: dict[str, str] = field(default_factory=dict)
    custom_commands: list[dict[str, Any]] = field(default_factory=list)


def config_dir() -> Path:
    """Return the per-user data directory.

    Honors the VOICEPILOT_CONFIG_DIR environment variable first, then
    %LOCALAPPDATA%\\VoicePilot on Windows and ~/.local/share/VoicePilot elsewhere.
    """
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "VoicePilot"
    return Path.home() / ".local" / "share" / "VoicePilot"


def load_config(path: Path | None = None) -> AppConfig:
    """Load defaults, then overlay the user config file.

    Args:
        path: User config file. Defaults to config_dir() / "config.toml".

    A missing or unreadable user file is logged and ignored. Within a readable file,
    each setting is checked on its own: a wrongly typed or out-of-range value is logged
    by key name and replaced by its default, while the other settings are kept. The
    application always starts with a usable configuration.
    """
    user_path = path if path is not None else config_dir() / CONFIG_FILE_NAME
    merged = _load_defaults_table()
    user = _read_user_table(user_path)
    if user:
        merged = _overlay(merged, user)
    return _from_table(merged)


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    """Write cfg as TOML. Unset optional values (overlay_pos=None) are omitted."""
    target = path if path is not None else config_dir() / CONFIG_FILE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomli_w.dumps(_to_table(cfg)), encoding="utf-8")


def _load_defaults_table() -> dict[str, Any]:
    """Parse the bundled defaults.toml."""
    text = resources.files("voicepilot").joinpath("resources", DEFAULTS_RESOURCE).read_text(encoding="utf-8")
    return tomllib.loads(text)


def _read_user_table(path: Path) -> dict[str, Any]:
    """Parse the user config file, returning an empty table if it is missing or unreadable.

    The file is decoded as UTF-8; a leading byte-order mark (written by some Windows
    editors) is accepted. Other encodings, such as UTF-16 or Latin-1, are rejected.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_bytes().decode("utf-8-sig")
        return tomllib.loads(text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("Ignoring unreadable config file %s: %s", path, type(exc).__name__)
        return {}


def _overlay(base: dict[str, Any], user: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay user values on base. Alias tables merge key by key; a non-table value is ignored."""
    result = dict(base)
    for key, value in user.items():
        if key in _ALIAS_TABLES:
            if isinstance(value, Mapping):
                result[key] = {**result.get(key, {}), **value}
            else:
                log.warning("Setting %s must be a table; using the default entries", key)
        else:
            result[key] = value
    return result


def _from_table(data: Mapping[str, Any]) -> AppConfig:
    """Build AppConfig from a merged TOML table, checking each setting on its own.

    A setting that is wrongly typed or out of range is logged by name and left at its
    default. The log never includes the offending value.
    """
    kwargs: dict[str, Any] = {}
    for f in fields(AppConfig):
        if f.name in {*_ALIAS_TABLES.values(), "custom_commands"} or f.name not in data:
            continue
        try:
            kwargs[f.name] = _checked_value(f.name, data[f.name])
        except (TypeError, ValueError):
            log.warning("Invalid value for setting %s; using the default", f.name)
    for table, attr in _ALIAS_TABLES.items():
        entries = data.get(table, {})
        if not isinstance(entries, Mapping):
            continue
        kwargs[attr] = _alias_entries(table, entries)
    from voicepilot.custom_commands import load_commands

    kwargs["custom_commands"] = load_commands(data.get("custom_commands", []))
    return AppConfig(**kwargs)


def _checked_value(name: str, value: Any) -> Any:
    """Return value converted to the type of setting name, or raise TypeError/ValueError."""
    if name == "stt_mode":
        return SttBackendMode(value)
    if name == "overlay_pos":
        return _checked_position(value)
    expected = type(getattr(AppConfig(), name))
    if type(value) is not expected and not (expected is float and type(value) is int):
        raise TypeError(f"{name} must be {expected.__name__}")
    if expected is float:
        value = float(value)
    if name in _NON_EMPTY_STRINGS and not value.strip():
        raise ValueError(f"{name} must not be empty")
    bounds = _RANGES.get(name)
    if bounds is not None and not bounds[0] <= value <= bounds[1]:
        raise ValueError(f"{name} must be between {bounds[0]} and {bounds[1]}")
    return value


def _checked_position(value: Any) -> tuple[int, int] | None:
    """Return an (x, y) pair of integers, None when unset, or raise ValueError/TypeError."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise TypeError("overlay_pos must be a pair of integers")
    x, y = value
    if any(type(item) is not int for item in (x, y)):
        raise TypeError("overlay_pos must be a pair of integers")
    return (x, y)


def _alias_entries(table: str, entries: Mapping[Any, Any]) -> dict[str, str]:
    """Keep the string entries of an alias table, logging how many were skipped."""
    result: dict[str, str] = {}
    skipped = 0
    for key, value in entries.items():
        if isinstance(value, str):
            result[str(key)] = value
        else:
            skipped += 1
    if skipped:
        log.warning("Ignored %d non-text entries in the [%s] table", skipped, table)
    return result


def _to_table(cfg: AppConfig) -> dict[str, Any]:
    """Convert AppConfig to a TOML-serializable table."""
    table: dict[str, Any] = {}
    for f in fields(AppConfig):
        if f.name in _ALIAS_TABLES.values():
            continue
        value = getattr(cfg, f.name)
        if value is None:
            continue
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, tuple):
            value = list(value)
        table[f.name] = value
    table["websites"] = dict(cfg.website_aliases)
    table["apps"] = dict(cfg.app_aliases)
    return table
