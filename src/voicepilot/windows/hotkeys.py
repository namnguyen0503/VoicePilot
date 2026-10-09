"""Global hotkeys via RegisterHotKey on a dedicated message-loop thread.

``parse_hotkey`` is pure and unit-tested on any platform. ``HotkeyManager`` loads
user32 and kernel32 lazily, so importing this module never touches Windows APIs.

Lifecycle: ``register`` each binding, then ``start``; ``stop`` unregisters everything
and clears the bindings. To change bindings after a settings change, call ``stop``
and register the new set on the same or a new manager.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import threading
from collections.abc import Callable
from ctypes import POINTER, Structure, c_int, c_size_t, c_ssize_t, c_uint32, c_void_p
from dataclasses import dataclass
from typing import Any

from voicepilot.windows.input import vk_for_key

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_START_TIMEOUT_S = 5.0
_STOP_TIMEOUT_S = 2.0

_MODIFIER_FLAGS: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse a spec such as "ctrl+alt+space" into (modifiers, virtual-key code).

    The returned modifiers always include MOD_NOREPEAT. A spec needs at least one
    modifier, so a global hotkey cannot swallow a plain letter.

    Raises:
        ValueError: If the spec has no modifier, no key, two keys, duplicate
            modifiers, an empty token or an unknown key name.
    """
    tokens = [token.strip().lower() for token in spec.split("+")]
    if any(not token for token in tokens):
        raise ValueError(f"Empty token in hotkey {spec!r}")
    modifiers = 0
    key_vk: int | None = None
    for token in tokens:
        if token in _MODIFIER_FLAGS:
            flag = _MODIFIER_FLAGS[token]
            if modifiers & flag:
                raise ValueError(f"Duplicate modifier {token!r} in {spec!r}")
            modifiers |= flag
            continue
        if key_vk is not None:
            raise ValueError(f"Hotkey {spec!r} has more than one key")
        key_vk = vk_for_key(token)
        if key_vk is None:
            raise ValueError(f"Unknown key {token!r} in {spec!r}")
    if key_vk is None:
        raise ValueError(f"Hotkey {spec!r} has no key")
    if modifiers == 0:
        raise ValueError(f"Hotkey {spec!r} needs at least one modifier")
    return modifiers | MOD_NOREPEAT, key_vk


class _POINT(Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _MSG(Structure):
    _fields_ = [
        ("hwnd", c_void_p),
        ("message", c_uint32),
        ("wParam", c_size_t),
        ("lParam", c_ssize_t),
        ("time", c_uint32),
        ("pt", _POINT),
    ]


@dataclass(frozen=True)
class _Binding:
    spec: str
    modifiers: int
    vk: int
    callback: Callable[[], None]


@functools.cache
def _user32() -> Any:
    """Load user32 and set the prototypes used by this module (Windows only)."""
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.RegisterHotKey.argtypes = [c_void_p, c_int, c_uint32, c_uint32]
    lib.RegisterHotKey.restype = c_int
    lib.UnregisterHotKey.argtypes = [c_void_p, c_int]
    lib.UnregisterHotKey.restype = c_int
    lib.GetMessageW.argtypes = [POINTER(_MSG), c_void_p, c_uint32, c_uint32]
    lib.GetMessageW.restype = c_int
    lib.PeekMessageW.argtypes = [POINTER(_MSG), c_void_p, c_uint32, c_uint32, c_uint32]
    lib.PeekMessageW.restype = c_int
    lib.PostThreadMessageW.argtypes = [c_uint32, c_uint32, c_size_t, c_ssize_t]
    lib.PostThreadMessageW.restype = c_int
    return lib


@functools.cache
def _kernel32() -> Any:
    """Load kernel32 (Windows only)."""
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.GetCurrentThreadId.restype = c_uint32
    return lib


class HotkeyManager:
    """Registers global hotkeys on its own thread and dispatches WM_HOTKEY to callbacks.

    Callbacks run on the hotkey thread, so they must hand work to the UI thread
    themselves. Exceptions raised by a callback are logged and do not stop the loop.
    """

    def __init__(self, on_error: Callable[[str, str], None] | None = None) -> None:
        """Create a manager.

        Args:
            on_error: Called as on_error(spec, message_vi) when a hotkey cannot be
                registered, for example because another application owns it.
                Defaults to logging a warning.
        """
        self._on_error = on_error or self._log_error
        self._bindings: dict[int, _Binding] = {}
        self._next_id = 1
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()

    def register(self, spec: str, callback: Callable[[], None]) -> None:
        """Queue a hotkey for registration when start() runs.

        Raises:
            ValueError: If spec cannot be parsed.
            RuntimeError: If called while the manager is running.
        """
        if self._thread is not None:
            raise RuntimeError("register() must be called before start() or after stop()")
        modifiers, vk = parse_hotkey(spec)
        self._bindings[self._next_id] = _Binding(spec, modifiers, vk, callback)
        self._next_id += 1

    def start(self) -> None:
        """Start the hotkey thread and wait until registration has been attempted."""
        if self._thread is not None:
            raise RuntimeError("HotkeyManager is already running")
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="voicepilot-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=_START_TIMEOUT_S)

    def stop(self) -> None:
        """Unregister all hotkeys, stop the thread and clear the bindings."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            _user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            thread.join(timeout=_STOP_TIMEOUT_S)
        self._thread = None
        self._bindings.clear()

    def _run(self) -> None:
        user32 = _user32()
        self._thread_id = int(_kernel32().GetCurrentThreadId())
        message = _MSG()
        # Creating the thread's message queue before stop() can post WM_QUIT to it.
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_NOREMOVE)
        registered: list[int] = []
        for hotkey_id, binding in self._bindings.items():
            if user32.RegisterHotKey(None, hotkey_id, binding.modifiers, binding.vk):
                registered.append(hotkey_id)
            else:
                code = ctypes.get_last_error()
                self._report(binding.spec, _describe_error(code))
        self._ready.set()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    if result < 0:
                        log.error("GetMessageW failed with error %d", ctypes.get_last_error())
                    break
                if message.message == WM_HOTKEY:
                    self._dispatch(int(message.wParam))
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)

    def _dispatch(self, hotkey_id: int) -> None:
        binding = self._bindings.get(hotkey_id)
        if binding is None:
            return
        try:
            binding.callback()
        except Exception:
            log.exception("Hotkey callback failed for %s", binding.spec)

    def _report(self, spec: str, message: str) -> None:
        try:
            self._on_error(spec, message)
        except Exception:
            log.exception("Hotkey error callback failed for %s", spec)

    @staticmethod
    def _log_error(spec: str, message: str) -> None:
        log.warning("Hotkey %s not registered: %s", spec, message)


def _describe_error(code: int) -> str:
    if code == ERROR_HOTKEY_ALREADY_REGISTERED:
        return "Phím tắt đang được ứng dụng khác dùng."
    return f"Không đăng ký được phím tắt (mã lỗi {code})."
