"""Unit tests for the installed-app index: matching, background refresh, TTL and the fallback walker.

Runs on any OS. The COM path is never called; loaders are fakes and the clock is a fake.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from voicepilot.windows import start_menu
from voicepilot.windows.start_menu import InstalledApps, match_app, normalize, walk_start_menu

JOIN_TIMEOUT_S = 1.0


def _join(apps: InstalledApps) -> None:
    thread = apps._thread
    assert thread is not None
    thread.join(JOIN_TIMEOUT_S)
    assert not thread.is_alive()


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_normalize_drops_case_diacritics_and_punctuation() -> None:
    assert normalize("  Gỡ cài đặt  ") == "go cai dat"
    assert normalize("Đồng hồ") == "dong ho"
    assert normalize("YouTube Music!") == "youtube music"


def test_exact_match_ignores_case() -> None:
    entries = [("Spotify", "spot.exe")]
    assert match_app("SPOTIFY", entries) == "spot.exe"


def test_exact_match_ignores_diacritics() -> None:
    entries = [("Zalo", "zalo.lnk")]
    assert match_app("zalo", entries) == "zalo.lnk"


def test_space_insensitive_match() -> None:
    entries = [("YouTube", "yt"), ("Spotify", "spot")]
    assert match_app("you tube", entries) == "yt"
    assert match_app("spot ify", entries) == "spot"


def test_prefix_match_picks_shortest_name() -> None:
    entries = [
        ("Visual Studio 2022 Installer", "installer"),
        ("Visual Studio Code - Insiders", "insiders"),
        ("Visual Studio Code", "code"),
    ]
    assert match_app("visual studio", entries) == "code"


def test_prefix_match_breaks_ties_alphabetically() -> None:
    entries = [("Zalo Pro B", "b"), ("Zalo Pro A", "a")]
    assert match_app("zalo", entries) == "a"


def test_spotify_prefix_matches_premium_name() -> None:
    assert match_app("spotify", [("Spotify Premium", "premium")]) == "premium"


def test_exact_match_preferred_over_longer_prefix() -> None:
    entries = [("Zalo Desktop Client", "long"), ("Zalo", "short")]
    assert match_app("zalo", entries) == "short"


def test_prefix_requires_leading_tokens_not_inner_tokens() -> None:
    assert match_app("code", [("Visual Studio Code", "code")]) is None


def test_uninstall_entries_are_ignored() -> None:
    entries = [("Uninstall Spotify", "uninstall.exe"), ("Spotify", "spot.exe")]
    assert match_app("spotify", entries) == "spot.exe"
    assert match_app("uninstall spotify", entries) is None


@pytest.mark.parametrize(
    "name",
    ["Gỡ cài đặt Zalo", "Zalo README", "Zalo Help", "Zalo Release Notes", "Zalo Website"],
)
def test_non_app_entries_are_ignored(name: str) -> None:
    assert match_app("zalo", [(name, "x")]) is None


def test_too_short_spoken_returns_none() -> None:
    assert match_app("zl", [("Zl", "x")]) is None
    assert match_app(" a ", [("A", "x")]) is None


def test_unknown_app_returns_none() -> None:
    assert match_app("notepad", [("Spotify", "spot.exe")]) is None


def test_empty_names_are_dropped() -> None:
    assert match_app("abc", [("   ", "blank"), ("!!!", "punct")]) is None


def test_find_returns_none_until_background_load_finishes() -> None:
    gate = threading.Event()

    def loader() -> list[start_menu.AppEntry]:
        gate.wait(JOIN_TIMEOUT_S)
        return [("Spotify", "spot.exe")]

    apps = InstalledApps(loader=loader, clock=FakeClock())
    assert apps.find("spotify") is None
    gate.set()
    _join(apps)
    assert apps.find("spotify") == "spot.exe"


def test_find_does_not_start_second_scan_while_one_runs() -> None:
    gate = threading.Event()
    calls: list[int] = []

    def loader() -> list[start_menu.AppEntry]:
        calls.append(1)
        gate.wait(JOIN_TIMEOUT_S)
        return []

    apps = InstalledApps(loader=loader, clock=FakeClock())
    apps.find("spotify")
    apps.find("spotify")
    apps.start_refresh()
    gate.set()
    _join(apps)
    assert len(calls) == 1


def test_ttl_expiry_triggers_refresh_with_fake_clock() -> None:
    clock = FakeClock(0.0)
    snapshots = [[("Spotify", "old.exe")], [("Spotify", "new.exe")]]
    calls: list[int] = []

    def loader() -> list[start_menu.AppEntry]:
        calls.append(1)
        return snapshots[len(calls) - 1]

    apps = InstalledApps(loader=loader, ttl_s=300.0, clock=clock)
    apps.find("spotify")
    _join(apps)
    assert apps.find("spotify") == "old.exe"
    assert len(calls) == 1

    clock.now = 300.0
    assert apps.find("spotify") == "old.exe"
    assert len(calls) == 1

    clock.now = 301.0
    assert apps.find("spotify") == "old.exe"  # answered from the stale snapshot while refreshing
    _join(apps)
    assert len(calls) == 2
    assert apps.find("spotify") == "new.exe"


def test_loader_failure_keeps_previous_snapshot_and_logs_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock(0.0)
    results: list[object] = [[("Spotify", "spot.exe")], RuntimeError("Secret Zalo Path")]

    def loader() -> list[start_menu.AppEntry]:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, list)
        return result

    apps = InstalledApps(loader=loader, ttl_s=300.0, clock=clock)
    apps.find("spotify")
    _join(apps)

    clock.now = 400.0
    with caplog.at_level(logging.WARNING, logger="voicepilot.windows.start_menu"):
        assert apps.find("spotify") == "spot.exe"
        _join(apps)
        assert apps.find("spotify") == "spot.exe"

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in messages
    assert "Secret Zalo Path" not in messages
    assert "Spotify" not in messages


def test_failed_scan_retries_only_after_ttl() -> None:
    clock = FakeClock(0.0)
    calls: list[int] = []

    def loader() -> list[start_menu.AppEntry]:
        calls.append(1)
        raise OSError("denied")

    apps = InstalledApps(loader=loader, ttl_s=300.0, clock=clock)
    apps.find("spotify")
    _join(apps)
    clock.now = 10.0
    apps.find("spotify")
    assert len(calls) == 1
    clock.now = 600.0
    apps.find("spotify")
    _join(apps)
    assert len(calls) == 2


def test_walk_start_menu_finds_lnk_under_each_root_and_ignores_other_files(tmp_path: Path) -> None:
    first = tmp_path / "all_users"
    second = tmp_path / "current_user"
    (first / "Spotify").mkdir(parents=True)
    (second / "Dev" / "Tools").mkdir(parents=True)
    (first / "Spotify" / "Spotify.lnk").write_bytes(b"")
    (second / "Dev" / "Tools" / "Visual Studio Code.lnk").write_bytes(b"")
    (second / "readme.txt").write_text("x", encoding="utf-8")
    (second / "Zalo.exe").write_bytes(b"")
    missing = tmp_path / "missing"

    entries = walk_start_menu([first, second, missing])

    assert sorted(entries) == sorted(
        [
            ("Spotify", str(first / "Spotify" / "Spotify.lnk")),
            ("Visual Studio Code", str(second / "Dev" / "Tools" / "Visual Studio Code.lnk")),
        ]
    )


def test_load_installed_apps_falls_back_to_start_menu_when_com_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Zalo.lnk").write_bytes(b"")

    def com_fails() -> list[start_menu.AppEntry]:
        raise ImportError("no pythoncom")

    monkeypatch.setattr(start_menu, "_load_from_appsfolder", com_fails)
    monkeypatch.setattr(start_menu, "start_menu_roots", lambda: [tmp_path])

    assert start_menu.load_installed_apps() == [("Zalo", str(tmp_path / "Zalo.lnk"))]


@pytest.mark.parametrize(
    ("path", "target"),
    [
        # Real Windows: desktop apps report a full path; "shell:AppsFolder\C:\..." would not launch.
        (r"C:\Apps\Roblox\RobloxPlayerBeta.exe", r"C:\Apps\Roblox\RobloxPlayerBeta.exe"),
        ("Spotify.Spotify_zpdnekdrzrea0!Spotify", r"shell:AppsFolder\Spotify.Spotify_zpdnekdrzrea0!Spotify"),
        (r"{1AC14E77-02E7}\notepad.exe", r"shell:AppsFolder\{1AC14E77-02E7}\notepad.exe"),
    ],
)
def test_apps_folder_target_launches_paths_directly_and_ids_through_the_shell(path: str, target: str) -> None:
    assert start_menu.apps_folder_target(path) == target
