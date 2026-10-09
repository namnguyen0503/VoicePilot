"""Keyboard input via SendInput: Unicode text, key chords and modifier release.

The event builders are pure and unit-tested on any platform. The ctypes adapter
(SendInput, GetAsyncKeyState) loads user32 lazily, so importing this module never
touches Windows-only APIs. Struct sizes use fixed-width ctypes types so the layout
matches the Win32 definitions on both 32-bit and 64-bit Python.
"""

from __future__ import annotations

import ctypes
import functools
import time
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from ctypes import POINTER, Structure, Union, c_int, c_size_t, c_uint16, c_uint32, c_ushort
from dataclasses import dataclass
from typing import Any

from voicepilot.contracts import ExecutionError

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1

MAX_EVENTS_PER_CALL = 64
INTER_BATCH_DELAY_S = 0.002

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
# Unassigned virtual-key code. Pressing it while Alt is held stops a lone Alt
# release from activating the target application's menu bar.
VK_CANCEL_MENU_ACTIVATION = 0xE8

RELEASABLE_MODIFIERS: tuple[int, ...] = (
    VK_LSHIFT,
    VK_RSHIFT,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_LMENU,
    VK_RMENU,
    VK_LWIN,
    VK_RWIN,
)
_ALT_KEYS = frozenset({VK_LMENU, VK_RMENU})
_EXTENDED_KEYS = frozenset(
    {
        VK_PRIOR,
        VK_NEXT,
        VK_END,
        VK_HOME,
        VK_LEFT,
        VK_UP,
        VK_RIGHT,
        VK_DOWN,
        VK_INSERT,
        VK_DELETE,
        VK_LWIN,
        VK_RWIN,
        VK_RCONTROL,
        VK_RMENU,
    }
)

_NAMED_KEYS: dict[str, int] = {
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "alt": VK_MENU,
    "shift": VK_SHIFT,
    "win": VK_LWIN,
    "windows": VK_LWIN,
    "tab": VK_TAB,
    "enter": VK_RETURN,
    "return": VK_RETURN,
    "esc": VK_ESCAPE,
    "escape": VK_ESCAPE,
    "space": VK_SPACE,
    "backspace": VK_BACK,
    "delete": VK_DELETE,
    "del": VK_DELETE,
    "insert": VK_INSERT,
    "home": VK_HOME,
    "end": VK_END,
    "pageup": VK_PRIOR,
    "pagedown": VK_NEXT,
    "left": VK_LEFT,
    "up": VK_UP,
    "right": VK_RIGHT,
    "down": VK_DOWN,
}


class KEYBDINPUT(Structure):
    """Win32 KEYBDINPUT."""

    _fields_ = [
        ("wVk", c_uint16),
        ("wScan", c_uint16),
        ("dwFlags", c_uint32),
        ("time", c_uint32),
        ("dwExtraInfo", c_size_t),
    ]


class MOUSEINPUT(Structure):
    """Win32 MOUSEINPUT; present so the INPUT union has its real size."""

    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", c_uint32),
        ("dwFlags", c_uint32),
        ("time", c_uint32),
        ("dwExtraInfo", c_size_t),
    ]


class HARDWAREINPUT(Structure):
    """Win32 HARDWAREINPUT; present so the INPUT union has its real size."""

    _fields_ = [("uMsg", c_uint32), ("wParamL", c_uint16), ("wParamH", c_uint16)]


class _InputUnion(Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(Structure):
    """Win32 INPUT. ``ki``/``mi``/``hi`` are reachable directly through the anonymous union."""

    _anonymous_ = ("u",)
    _fields_ = [("type", c_uint32), ("u", _InputUnion)]


@dataclass(frozen=True)
class KeyEvent:
    """One keyboard event before it is converted to a native INPUT structure.

    Attributes:
        vk: Virtual-key code, or 0 for Unicode events.
        scan: Scan code, or the UTF-16 code unit for Unicode events.
        flags: KEYEVENTF_* flags.
    """

    vk: int
    scan: int = 0
    flags: int = 0


def vk_for_key(name: str) -> int | None:
    """Map a key name such as "ctrl", "tab", "f5", "a" or "7" to a virtual-key code."""
    key = name.strip().lower()
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if len(key) == 1 and key.isascii():
        if key.isalpha():
            return ord(key.upper())
        if key.isdigit():
            return ord(key)
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x6F + number  # VK_F1 is 0x70
    return None


def build_unicode_events(text: str) -> list[KeyEvent]:
    """Build SendInput events that type text as Unicode, independent of the keyboard layout.

    Characters outside the BMP are sent as UTF-16 surrogate pairs. "\\n" presses Enter,
    "\\t" presses Tab, "\\r" is dropped, and other control characters are skipped.
    """
    events: list[KeyEvent] = []
    for char in unicodedata.normalize("NFC", text):
        if char == "\r":
            continue
        if char in ("\n", "\t"):
            events.extend(_tap(VK_RETURN if char == "\n" else VK_TAB))
            continue
        if ord(char) < 0x20 or ord(char) == 0x7F:
            continue
        for unit in _utf16_units(char):
            events.append(KeyEvent(0, unit, KEYEVENTF_UNICODE))
            events.append(KeyEvent(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return events


def build_key_sequence(keys: Sequence[str]) -> list[KeyEvent]:
    """Press the given keys in order, then release them in reverse order.

    Raises:
        ValueError: If no keys are given or a key name is unknown.
    """
    if not keys:
        raise ValueError("At least one key is required")
    codes: list[int] = []
    for name in keys:
        code = vk_for_key(name)
        if code is None:
            raise ValueError(f"Unknown key: {name!r}")
        codes.append(code)
    downs = [KeyEvent(code, 0, _extended_flag(code)) for code in codes]
    ups = [KeyEvent(code, 0, _extended_flag(code) | KEYEVENTF_KEYUP) for code in reversed(codes)]
    return downs + ups


def release_events(held_vks: Iterable[int]) -> list[KeyEvent]:
    """Build key-up events for the modifiers that are still held down.

    When Alt is among them, a harmless key press is sent first so that releasing Alt
    does not open the target application's menu bar.
    """
    held = set(held_vks)
    to_release = [vk for vk in RELEASABLE_MODIFIERS if vk in held]
    if not to_release:
        return []
    events: list[KeyEvent] = []
    if _ALT_KEYS & held:
        events.extend(_tap(VK_CANCEL_MENU_ACTIVATION))
    events.extend(KeyEvent(vk, 0, _extended_flag(vk) | KEYEVENTF_KEYUP) for vk in to_release)
    return events


def chunked(events: Sequence[KeyEvent], size: int) -> Iterator[list[KeyEvent]]:
    """Split events into consecutive batches of at most size items."""
    items = list(events)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def release_modifiers() -> None:
    """Release Ctrl, Alt, Shift and Win if they are physically down.

    Called before every injected keystroke so that a modifier still held from the
    hotkey does not turn typed text into shortcuts.
    """
    user32 = _user32()
    held = [vk for vk in RELEASABLE_MODIFIERS if user32.GetAsyncKeyState(vk) & 0x8000]
    _send_events(release_events(held))


def type_unicode(text: str) -> None:
    """Type text at the caret of the focused window.

    Raises:
        ExecutionError: If SendInput did not accept every event (for example UIPI blocks the target).
    """
    release_modifiers()
    _send_events(build_unicode_events(text))


def send_hotkey(*keys: str) -> None:
    """Press a key chord such as send_hotkey("ctrl", "tab").

    Raises:
        ValueError: If a key name is unknown.
        ExecutionError: If SendInput did not accept every event.
    """
    events = build_key_sequence(keys)
    release_modifiers()
    _send_events(events)


def _tap(vk: int) -> list[KeyEvent]:
    return [KeyEvent(vk, 0, _extended_flag(vk)), KeyEvent(vk, 0, _extended_flag(vk) | KEYEVENTF_KEYUP)]


def _extended_flag(vk: int) -> int:
    return KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_KEYS else 0


def _utf16_units(char: str) -> list[int]:
    data = char.encode("utf-16-le", "replace")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


@functools.cache
def _user32() -> Any:
    """Load user32 and set the prototypes used by this module (Windows only)."""
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.SendInput.argtypes = [c_uint32, POINTER(INPUT), c_int]
    lib.SendInput.restype = c_uint32
    lib.GetAsyncKeyState.argtypes = [c_int]
    lib.GetAsyncKeyState.restype = c_ushort
    return lib


def _send_events(events: Sequence[KeyEvent]) -> None:
    """Send events through SendInput in batches.

    Raises:
        ExecutionError: If a batch is only partly accepted.
    """
    if not events:
        return
    user32 = _user32()
    batches = list(chunked(events, MAX_EVENTS_PER_CALL))
    for index, batch in enumerate(batches):
        array = (INPUT * len(batch))()
        for slot, event in zip(array, batch, strict=True):
            slot.type = INPUT_KEYBOARD
            slot.ki.wVk = event.vk
            slot.ki.wScan = event.scan
            slot.ki.dwFlags = event.flags
            slot.ki.time = 0
            slot.ki.dwExtraInfo = 0
        sent = user32.SendInput(len(batch), array, ctypes.sizeof(INPUT))
        if sent != len(batch):
            raise ExecutionError(f"SendInput accepted {sent} of {len(batch)} events")
        if index < len(batches) - 1:
            time.sleep(INTER_BATCH_DELAY_S)
