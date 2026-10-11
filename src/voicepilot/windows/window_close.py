"""Identify desktop/taskbar windows before sending the close-window shortcut."""

from __future__ import annotations

import ctypes
import functools
import os
from typing import Any

_DESKTOP_CLASSES = frozenset({
    "progman", "workerw", "shell_traywnd", "shell_secondarytraywnd",
    "shelldll_defview", "#32769",
})


@functools.cache
def _user32() -> Any:
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes = [ctypes.c_void_p]
    user.IsWindow.restype = ctypes.c_int
    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetAncestor.restype = ctypes.c_void_p
    user.GetShellWindow.restype = ctypes.c_void_p
    user.GetDesktopWindow.restype = ctypes.c_void_p
    user.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user.GetClassNameW.restype = ctypes.c_int
    return user


def is_closable_window(hwnd: int, *, user32: Any = None) -> bool:
    """Fail closed on missing facts; allow Explorer folder windows, but never its desktop.

    Check both the captured HWND and its top-level ancestor. Window titles and executable
    names cannot distinguish an Explorer folder from the desktop reliably.
    """
    if not hwnd or (user32 is None and os.name != "nt"):
        return False
    try:
        user = user32 if user32 is not None else _user32()
        if not user.IsWindow(hwnd):
            return False
        root = int(user.GetAncestor(hwnd, 2) or 0)  # GA_ROOT
        shell = int(user.GetShellWindow() or 0)
        desktop = int(user.GetDesktopWindow() or 0)
        if not root or not shell or not desktop or hwnd in (shell, desktop) or root in (shell, desktop):
            return False
        for handle in {hwnd, root}:
            name = ctypes.create_unicode_buffer(256)
            if not user.GetClassNameW(handle, name, len(name)):
                return False
            if name.value.casefold() in _DESKTOP_CLASSES:
                return False
        return True
    except Exception:
        return False
