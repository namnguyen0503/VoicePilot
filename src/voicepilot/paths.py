"""Locations of application files for source checkouts and frozen (PyInstaller) builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

MODELS_ENV_VAR = "VOICEPILOT_MODELS_DIR"


def app_base_dir() -> Path:
    """Return the directory that holds bundled files.

    In a frozen build this is the folder containing VoicePilot.exe; in a source
    checkout it is the repository root (the parent of the ``src`` directory).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def models_dir() -> Path:
    """Return the directory that holds speech models.

    The VOICEPILOT_MODELS_DIR environment variable overrides the default
    ``<app_base_dir>/models``.
    """
    override = os.environ.get(MODELS_ENV_VAR)
    if override:
        return Path(override)
    return app_base_dir() / "models"
