"""Tests for the Start-with-Windows Run entry, using an in-memory registry fake."""

from __future__ import annotations

import logging

import pytest

from voicepilot.windows import autostart
from voicepilot.windows.autostart import VALUE_NAME, WinregRunStore, quoted_command, sync

EXE = r"C:\Portable Apps\VoicePilot\VoicePilot.exe"


class FakeRunStore:
    """Holds the one Run value in memory. fail_with makes every call raise that exception."""

    def __init__(self, value: str | None = None, fail_with: Exception | None = None) -> None:
        self.value = value
        self.fail_with = fail_with
        self.writes: list[str] = []
        self.deletes = 0

    def read(self) -> str | None:
        if self.fail_with is not None:
            raise self.fail_with
        return self.value

    def write(self, command: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.writes.append(command)
        self.value = command

    def delete(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.deletes += 1
        self.value = None


class FakeWinreg:
    """Just enough of the winreg module to exercise WinregRunStore."""

    HKEY_CURRENT_USER = 1
    KEY_QUERY_VALUE = 2
    KEY_SET_VALUE = 4
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.opened: list[tuple[str, int]] = []

    class _Key:
        def __init__(self, outer: FakeWinreg) -> None:
            self.outer = outer

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

    def OpenKey(self, hive, path, reserved, access):  # noqa: N802 - winreg API name
        self.opened.append((path, access))
        return self._Key(self)

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_enabled_and_frozen_writes_quoted_exe_path() -> None:
    store = FakeRunStore()

    assert sync(True, store=store, frozen=True, executable=EXE) is True

    assert store.writes == ['"C:\\Portable Apps\\VoicePilot\\VoicePilot.exe"']
    assert store.value == quoted_command(EXE)


def test_enabled_refreshes_stale_path_after_the_folder_moved() -> None:
    store = FakeRunStore(value=r'"D:\Old\VoicePilot.exe"')

    assert sync(True, store=store, frozen=True, executable=EXE) is True

    assert store.writes == [quoted_command(EXE)]


def test_enabled_with_matching_entry_writes_nothing_and_ignores_case() -> None:
    store = FakeRunStore(value=quoted_command(EXE).upper())

    assert sync(True, store=store, frozen=True, executable=EXE) is True

    assert store.writes == []


def test_disabled_removes_existing_value() -> None:
    store = FakeRunStore(value=quoted_command(EXE))

    assert sync(False, store=store, frozen=True, executable=EXE) is True

    assert store.deletes == 1
    assert store.value is None


def test_disabled_without_value_deletes_nothing() -> None:
    store = FakeRunStore()

    assert sync(False, store=store, frozen=True, executable=EXE) is True

    assert store.deletes == 0


@pytest.mark.parametrize("enabled", [True, False])
def test_not_frozen_never_touches_the_registry(enabled: bool, caplog: pytest.LogCaptureFixture) -> None:
    store = FakeRunStore()
    caplog.set_level(logging.INFO, logger=autostart.log.name)

    assert sync(enabled, store=store, frozen=False, executable="python.exe") is False

    assert store.writes == []
    assert store.deletes == 0
    assert store.value is None


def test_oserror_is_swallowed_and_logged_by_type_only(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeRunStore(fail_with=PermissionError(r"denied: C:\secret\path"))
    caplog.set_level(logging.WARNING, logger=autostart.log.name)

    assert sync(True, store=store, frozen=True, executable=EXE) is False

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "PermissionError" in messages
    assert "secret" not in messages
    assert EXE not in messages


def test_missing_winreg_is_reported_as_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_winreg(*_args, **_kwargs):
        raise ImportError("No module named 'winreg'")

    monkeypatch.setattr(autostart, "WinregRunStore", no_winreg)

    assert sync(True, frozen=True, executable=EXE) is False


def test_winreg_store_writes_and_reads_the_value_name() -> None:
    fake = FakeWinreg()
    store = WinregRunStore(fake)

    store.write(quoted_command(EXE))

    assert fake.values == {VALUE_NAME: quoted_command(EXE)}
    assert store.read() == quoted_command(EXE)
    assert fake.opened[0][0] == r"Software\Microsoft\Windows\CurrentVersion\Run"


def test_winreg_store_reads_none_and_delete_is_idempotent() -> None:
    fake = FakeWinreg()
    store = WinregRunStore(fake)

    assert store.read() is None
    store.delete()  # absent value: no error

    fake.values[VALUE_NAME] = quoted_command(EXE)
    store.delete()
    assert fake.values == {}


def test_sync_with_winreg_store_end_to_end() -> None:
    fake = FakeWinreg()
    store = WinregRunStore(fake)

    assert sync(True, store=store, frozen=True, executable=EXE) is True
    assert fake.values[VALUE_NAME] == quoted_command(EXE)

    assert sync(False, store=store, frozen=True, executable=EXE) is True
    assert VALUE_NAME not in fake.values
