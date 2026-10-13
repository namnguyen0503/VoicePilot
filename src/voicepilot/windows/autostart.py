"""Start VoicePilot with Windows through the per-user Run key.

The entry lives in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run and holds the quoted
path of the running executable. Only a frozen (PyInstaller) build registers anything, so a
development run never points the Run key at python.exe.

winreg is imported lazily, so this module imports on any OS. Tests pass a fake store.
Failures are logged by error type only, never by path.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Protocol

log = logging.getLogger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VoicePilot"


class RunStore(Protocol):
    """Read, write and delete the single Run value. read() returns None when it is absent."""

    def read(self) -> str | None: ...

    def write(self, command: str) -> None: ...

    def delete(self) -> None: ...


class WinregRunStore:
    """RunStore backed by the registry. winreg is imported when the store is created."""

    def __init__(self, winreg_module: Any = None) -> None:
        if winreg_module is None:
            import winreg as winreg_module  # noqa: PLC0415 - Windows-only, imported lazily
        self._winreg = winreg_module

    def _open(self, access: int) -> Any:
        return self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)

    def read(self) -> str | None:
        try:
            with self._open(self._winreg.KEY_QUERY_VALUE) as key:
                value, _kind = self._winreg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def write(self, command: str) -> None:
        with self._open(self._winreg.KEY_SET_VALUE) as key:
            self._winreg.SetValueEx(key, VALUE_NAME, 0, self._winreg.REG_SZ, command)

    def delete(self) -> None:
        try:
            with self._open(self._winreg.KEY_SET_VALUE) as key:
                self._winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass


def quoted_command(executable: str) -> str:
    """Return the Run value for an executable path, quoted so spaces in the path are safe."""
    return f'"{executable}"'


def _same_command(current: str, expected: str) -> bool:
    # Windows paths are case-insensitive.
    return current.strip().casefold() == expected.casefold()


def sync(
    enabled: bool,
    *,
    store: RunStore | None = None,
    frozen: bool | None = None,
    executable: str | None = None,
) -> bool:
    """Make the Run entry match enabled. Only a frozen build is changed.

    Enabled: writes the entry when it is missing or points at another path (the portable
    folder may have moved). Disabled: removes the entry when present.

    Args:
        enabled: The start_with_windows setting.
        store: Registry access. None uses the real Run key (imported lazily).
        frozen: Whether the app runs as a frozen build. None reads sys.frozen.
        executable: Path to register. None uses sys.executable.

    Returns:
        True when the Run entry now matches enabled. False when nothing was done (not a frozen
        build, or unsupported platform) or when the registry access failed.
    """
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        log.info("autostart not changed: not a packaged build")
        return False
    try:
        run_store = store if store is not None else WinregRunStore()
        current = run_store.read()
        if enabled:
            expected = quoted_command(executable if executable is not None else sys.executable)
            if current is not None and _same_command(current, expected):
                return True
            run_store.write(expected)
            log.info("autostart entry written")
            return True
        if current is not None:
            run_store.delete()
            log.info("autostart entry removed")
        return True
    except ImportError:
        log.warning("autostart unsupported on this platform")
        return False
    except OSError as exc:
        log.warning("autostart update failed: %s", type(exc).__name__)
        return False
