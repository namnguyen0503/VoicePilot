"""Edge/Chrome control via Windows input on a verified window: open site, search, tab keys.

URL validation, search URL building and the tab hotkey table are pure and unit-tested.
Launching uses ShellExecuteW (Windows only, loaded lazily) and falls back to the default
browser through os.startfile when the requested browser cannot be started.
"""

from __future__ import annotations

import ctypes
import functools
import os
from typing import Any
from urllib.parse import urlencode, urlsplit

from voicepilot.contracts import AppKind, ExecutionError, Intent, WindowContext

BROWSER_EXECUTABLES: dict[str, str] = {"edge": "msedge.exe", "chrome": "chrome.exe"}
SEARCH_ENDPOINT = "https://www.google.com/search"

TAB_HOTKEYS: dict[Intent, tuple[str, ...]] = {
    Intent.BROWSER_NEXT_TAB: ("ctrl", "tab"),
    Intent.BROWSER_PREV_TAB: ("ctrl", "shift", "tab"),
    Intent.BROWSER_NEW_TAB: ("ctrl", "t"),
    Intent.BROWSER_CLOSE_TAB: ("ctrl", "w"),
}
TAB_MESSAGES: dict[Intent, str] = {
    Intent.BROWSER_NEXT_TAB: "Đã chuyển tab tiếp",
    Intent.BROWSER_PREV_TAB: "Đã chuyển tab trước",
    Intent.BROWSER_NEW_TAB: "Đã mở tab mới",
    Intent.BROWSER_CLOSE_TAB: "Đã đóng tab",
}

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_SW_SHOWNORMAL = 1
_SHELL_SUCCESS_THRESHOLD = 32


def validate_url(url: str) -> str | None:
    """Return the stripped URL when it is an absolute http(s) URL without whitespace, else None."""
    candidate = url.strip()
    if not candidate or any(char.isspace() or ord(char) < 0x20 for char in candidate):
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in _ALLOWED_SCHEMES or not parts.hostname:
        return None
    return candidate


def build_search_url(query: str) -> str:
    """Return the Google search URL for query, with UTF-8 percent-encoding (spaces as '+')."""
    return f"{SEARCH_ENDPOINT}?{urlencode({'q': query.strip()})}"


def launch_url(url: str, ctx: WindowContext) -> None:
    """Open url in a new tab of the browser in ctx, or in the default browser otherwise.

    The URL must already be validated by the caller.

    Raises:
        ExecutionError: If neither the browser nor the default browser can open the URL.
    """
    executable = BROWSER_EXECUTABLES.get(ctx.browser or "") if ctx.app_kind is AppKind.BROWSER else None
    if executable is not None and _shell_open(executable, url):
        return
    try:
        os.startfile(url)  # type: ignore[attr-defined]  # Windows-only API
    except OSError as exc:
        raise ExecutionError("Cannot open the URL in a browser") from exc


def _shell_open(file: str, parameters: str) -> bool:
    """Run ShellExecuteW with the "open" verb and report whether it succeeded (Windows only)."""
    shell32 = _shell32()
    result = shell32.ShellExecuteW(None, "open", file, parameters, None, _SW_SHOWNORMAL)
    return (result or 0) > _SHELL_SUCCESS_THRESHOLD


@functools.cache
def _shell32() -> Any:
    """Load shell32 and set the prototype used by this module (Windows only)."""
    lib = ctypes.WinDLL("shell32")
    lib.ShellExecuteW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    lib.ShellExecuteW.restype = ctypes.c_void_p
    return lib
