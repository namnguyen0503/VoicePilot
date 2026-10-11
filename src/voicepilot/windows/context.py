"""Foreground window capture and re-validation via Win32 (user32, kernel32); implements ContextProvider.

The helpers (classify_process, build_context, same_target) are pure and unit-tested on
any platform. The ctypes adapter loads user32 and kernel32 lazily, so importing this
module never touches Windows-only APIs.
"""

from __future__ import annotations

import ctypes
import functools
import os
from typing import Any

from voicepilot.contracts import AppKind, WindowContext

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_MAX_PATH_CHARS = 1024
_MAX_TITLE_CHARS = 512

_POWERPOINT_EXE = "powerpnt.exe"
_BROWSER_EXES: dict[str, str] = {"msedge.exe": "edge", "chrome.exe": "chrome"}


def process_basename(path: str) -> str:
    """Return the file name of an executable path, accepting Windows or POSIX separators."""
    return path.replace("/", "\\").rsplit("\\", 1)[-1].strip()


def classify_process(process_name: str) -> tuple[AppKind, str | None]:
    """Classify an executable name or path, case-insensitively.

    Returns:
        (AppKind, browser). browser is "edge" or "chrome" for browsers and None otherwise.
        An empty name is AppKind.NONE.
    """
    name = process_basename(process_name).lower()
    if not name:
        return AppKind.NONE, None
    if name == _POWERPOINT_EXE:
        return AppKind.POWERPOINT, None
    if name in _BROWSER_EXES:
        return AppKind.BROWSER, _BROWSER_EXES[name]
    return AppKind.OTHER, None


def none_context() -> WindowContext:
    """Return the context used when no usable window is in the foreground."""
    return WindowContext(hwnd=0, pid=0, process_name="", title="", app_kind=AppKind.NONE, browser=None)


def build_context(*, hwnd: int, pid: int, image_path: str, title: str, own_pid: int) -> WindowContext:
    """Build a WindowContext from raw foreground-window facts.

    Windows owned by this process (own_pid) and windows without a readable executable
    produce the NONE context, so VoicePilot never targets its own UI.
    """
    if not hwnd or not pid or pid == own_pid:
        return none_context()
    name = process_basename(image_path)
    kind, browser = classify_process(name)
    if kind is AppKind.NONE:
        return none_context()
    return WindowContext(hwnd=hwnd, pid=pid, process_name=name, title=title, app_kind=kind, browser=browser)


def same_target(ctx: WindowContext, hwnd: int, pid: int) -> bool:
    """True when the foreground window (hwnd, pid) is still the one captured in ctx."""
    return ctx.hwnd != 0 and hwnd == ctx.hwnd and pid == ctx.pid


@functools.cache
def _user32() -> Any:
    """Load user32 and set the prototypes used by this module (Windows only)."""
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.GetForegroundWindow.restype = ctypes.c_void_p
    lib.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.GetWindowThreadProcessId.restype = ctypes.c_uint32
    lib.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    lib.GetWindowTextW.restype = ctypes.c_int
    lib.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    lib.SetForegroundWindow.restype = ctypes.c_int
    lib.IsWindow.argtypes = [ctypes.c_void_p]
    lib.IsWindow.restype = ctypes.c_int
    return lib


@functools.cache
def _kernel32() -> Any:
    """Load kernel32 and set the prototypes used by this module (Windows only)."""
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    lib.OpenProcess.restype = ctypes.c_void_p
    lib.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    lib.QueryFullProcessImageNameW.restype = ctypes.c_int
    lib.CloseHandle.argtypes = [ctypes.c_void_p]
    lib.CloseHandle.restype = ctypes.c_int
    return lib


def _foreground() -> tuple[int, int]:
    """Return (hwnd, pid) of the foreground window, or (0, 0) when there is none."""
    hwnd = int(_user32().GetForegroundWindow() or 0)
    if not hwnd:
        return 0, 0
    pid = ctypes.c_uint32(0)
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, int(pid.value)


def _window_title(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(_MAX_TITLE_CHARS)
    _user32().GetWindowTextW(hwnd, buffer, _MAX_TITLE_CHARS)
    return buffer.value


def _image_path(pid: int) -> str:
    """Return the full executable path of pid, or "" when it cannot be read."""
    kernel = _kernel32()
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
        size = ctypes.c_uint32(_MAX_PATH_CHARS)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel.CloseHandle(handle)


class Win32ContextProvider:
    """Reads the foreground window with Win32 calls; implements contracts.ContextProvider."""

    def capture(self) -> WindowContext:
        """Snapshot the foreground window, or return the NONE context when it is unusable."""
        hwnd, pid = _foreground()
        if not hwnd:
            return none_context()
        return build_context(
            hwnd=hwnd,
            pid=pid,
            image_path=_image_path(pid),
            title=_window_title(hwnd),
            own_pid=os.getpid(),
        )

    def is_still_target(self, ctx: WindowContext) -> bool:
        """True when the current foreground window is the one captured in ctx."""
        hwnd, pid = _foreground()
        return same_target(ctx, hwnd, pid)


def restore_text_target(ctx: WindowContext, composer_hwnd: int) -> bool:
    """Restore only from our composer, after checking HWND identity to reject reuse.

    Never redirect input if the user switched to another external window while typing.
    No thread attachment or simulated focus-stealing keystrokes are used.
    """
    hwnd, pid = _foreground()
    if same_target(ctx, hwnd, pid):
        return True
    if hwnd != composer_hwnd or pid != os.getpid():
        return False
    if not ctx.hwnd:
        return True  # Only actions that do not require a window can execute.
    user = _user32()
    target_pid = ctypes.c_uint32()
    user.GetWindowThreadProcessId(ctx.hwnd, ctypes.byref(target_pid))
    if not user.IsWindow(ctx.hwnd) or target_pid.value != ctx.pid:
        return False
    if not user.SetForegroundWindow(ctx.hwnd):
        return False
    hwnd, pid = _foreground()
    return same_target(ctx, hwnd, pid)
