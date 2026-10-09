"""Shared pytest setup: offscreen Qt and an isolated per-test config directory."""

from __future__ import annotations

import os

# Must run before any Qt module is imported so that no display is needed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from voicepilot.config import CONFIG_ENV_VAR  # noqa: E402


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point VOICEPILOT_CONFIG_DIR at an empty temporary directory and return it."""
    home = tmp_path / "VoicePilot"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(home))
    return home
