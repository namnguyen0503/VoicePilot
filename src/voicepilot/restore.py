"""Separate prompt/settings restoration from destructive app-data factory reset."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from voicepilot.config import AppConfig, config_dir, default_config, save_config

RESET_MARKER = "factory-reset.pending"
RESET_REQUEST = "VoicePilot factory reset v1\n"


def restored_config(config: AppConfig, kind: str) -> AppConfig:
    """Build a detached reset draft; neither prompt nor settings reset removes custom."""
    if kind == "prompts":
        return replace(deepcopy(config), builtin_patterns={})
    if kind == "settings":
        return replace(default_config(), custom_commands=deepcopy(config.custom_commands))
    if kind == "factory":
        return default_config()
    raise ValueError("Unknown restore kind")


def _checked_root(root: Path) -> Path:
    """Require a dedicated app-data directory, outside installation and protected roots."""
    if root.is_symlink() or root.is_junction():
        raise ValueError("Thư mục dữ liệu không được là liên kết.")
    target = root.resolve()
    if target.name.casefold() != "voicepilot":
        raise ValueError("Factory reset cần thư mục dữ liệu riêng có tên VoicePilot.")
    home = Path.home().resolve()
    forbidden = {home, Path.cwd().resolve(), Path(sys.executable).resolve().parent,
                 Path(target.anchor), home.parent}
    if target in forbidden or target in home.parents:
        raise ValueError("Không thể khôi phục thư mục này.")
    if any(parent.is_symlink() or parent.is_junction() for parent in root.absolute().parents):
        raise ValueError("Đường dẫn dữ liệu không được đi qua liên kết.")
    # An environment override is supported for isolated runs, but never a directory
    # containing application sources, executables or installed models.
    if any((target / name).exists() for name in (".git", "src", "models", "VoicePilot.exe")):
        raise ValueError("Thư mục này chứa bản cài đặt, không phải dữ liệu app.")
    return target


def _reject_reparse_points(root: Path):
    """Preflight the whole tree; never traverse Windows junctions during a reset."""
    for base, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            item = Path(base) / name
            attrs = getattr(item.lstat(), "st_file_attributes", 0)
            if item.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("Dữ liệu có liên kết; chưa xóa dữ liệu nào.")


def request_factory_reset(root: Path | None = None):
    """Queue reset after shutdown, so Windows log/model handles cannot race deletion."""
    target = _checked_root(root if root is not None else config_dir())
    target.mkdir(parents=True, exist_ok=True)
    _reject_reparse_points(target)
    (target / RESET_MARKER).write_text(RESET_REQUEST, encoding="utf-8")


def complete_factory_reset(root: Path | None = None) -> bool:
    """Consume a requested reset at startup, before opening logs or loading user config."""
    target = root if root is not None else config_dir()
    if not (target / RESET_MARKER).exists():
        return False
    target = _checked_root(target)
    _reject_reparse_points(target)
    if (target / RESET_MARKER).read_text(encoding="utf-8") != RESET_REQUEST:
        raise ValueError("Yêu cầu factory reset không hợp lệ; chưa xóa dữ liệu.")
    if (target / "power-state.json").exists() and sys.platform == "win32":
        from voicepilot.contracts import ResultStatus
        from voicepilot.windows.system_controls import PowerPlanManager

        result = PowerPlanManager(state_path=target / "power-state.json").restore()
        if result.status is not ResultStatus.OK:
            raise OSError("Không khôi phục được power plan trước khi factory reset.")
    # Every child belongs to this dedicated data directory. Preserve the request
    # until cleanup and default configuration succeed, allowing a retry on failure.
    for child in target.iterdir():
        if child.name == RESET_MARKER:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    save_config(default_config(), target / "config.toml")
    (target / RESET_MARKER).unlink()
    return True
