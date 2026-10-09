"""Launch applications from aliases and capture screenshots of the target window with mss.

Also hosts the shared file helpers (known folders, unique_path) used by the PowerPoint
and screenshot code. Pure helpers are unit-tested on any platform; Win32 and mss are
imported lazily.
"""

from __future__ import annotations

import ctypes
import functools
import os
import sys
import time
import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from voicepilot.contracts import ExecutionError, WindowContext

Rect = tuple[int, int, int, int]  # left, top, right, bottom

_FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"
_FOLDERID_PICTURES = "33E28130-4E1E-4676-835A-98395C3BC3BB"
_OVERLAY_HIDE_SETTLE_S = 0.15
_SCREENSHOT_DIR = "VoicePilot"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    ]


def documents_dir() -> Path:
    """Return the current user's Documents folder."""
    return _known_folder(_FOLDERID_DOCUMENTS, Path.home() / "Documents")


def pictures_dir() -> Path:
    """Return the current user's Pictures folder."""
    return _known_folder(_FOLDERID_PICTURES, Path.home() / "Pictures")


def unique_path(directory: Path, filename: str) -> Path:
    """Return directory/filename, or "name (2).ext", "name (3).ext"... when it already exists."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    source = Path(filename)
    number = 2
    while True:
        candidate = directory / f"{source.stem} ({number}){source.suffix}"
        if not candidate.exists():
            return candidate
        number += 1


def pick_monitor_index(window: Rect, monitors: Sequence[Rect]) -> int:
    """Return the index of the monitor that overlaps the window the most, or 0 when none does."""
    best_index = 0
    best_area = 0
    for index, monitor in enumerate(monitors):
        area = _overlap_area(window, monitor)
        if area > best_area:
            best_index, best_area = index, area
    return best_index


def open_app(target: str) -> None:
    """Launch an application alias such as "POWERPNT.EXE", "msedge" or "calc.exe".

    Windows resolves the alias through App Paths or the shell.

    Raises:
        ExecutionError: If the shell cannot start the target.
    """
    try:
        os.startfile(target)  # type: ignore[attr-defined]  # Windows-only API
    except OSError as exc:
        raise ExecutionError(f"Cannot start {target}") from exc


def screenshot(
    ctx: WindowContext, hide_overlay: Callable[[], AbstractContextManager[Any]] | None = None
) -> Path:
    """Capture the monitor that contains the target window and save it as a PNG.

    The overlay is hidden through hide_overlay while the grab happens. The file goes to
    Pictures\\VoicePilot with a timestamped name.
    """
    import mss
    import mss.tools

    with mss.mss() as grabber:
        monitors = grabber.monitors[1:] or grabber.monitors
        index = 0
        window = window_rect(ctx.hwnd) if ctx.hwnd else None
        if window is not None:
            index = pick_monitor_index(window, [_to_rect(m) for m in monitors])
        context = hide_overlay() if hide_overlay is not None else nullcontext()
        with context:
            time.sleep(_OVERLAY_HIDE_SETTLE_S)
            shot = grabber.grab(monitors[index])
    folder = pictures_dir() / _SCREENSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder, f"VoicePilot_{datetime.now():%Y%m%d_%H%M%S}.png")
    mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    return path


def window_rect(hwnd: int) -> Rect | None:
    """Return the window rectangle in screen coordinates, or None when it cannot be read."""
    rect = _RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _to_rect(monitor: dict[str, int]) -> Rect:
    right = monitor["left"] + monitor["width"]
    bottom = monitor["top"] + monitor["height"]
    return (monitor["left"], monitor["top"], right, bottom)


def _overlap_area(first: Rect, second: Rect) -> int:
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    if width <= 0 or height <= 0:
        return 0
    return width * height


def _known_folder(folder_id: str, fallback: Path) -> Path:
    """Resolve a Known Folder through SHGetKnownFolderPath, falling back when unavailable."""
    if sys.platform != "win32":
        return fallback
    guid = _GUID.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    out = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32")
    hresult = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
    if hresult != 0 or not out.value:
        return fallback
    path = Path(out.value)
    ctypes.WinDLL("ole32").CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
    return path


@functools.cache
def _user32() -> Any:
    """Load user32 and set the prototypes used by this module (Windows only)."""
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    lib.GetWindowRect.restype = ctypes.c_int
    return lib
