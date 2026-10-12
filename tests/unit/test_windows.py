"""Unit tests for the Windows layer, run on Linux through pure helpers and injected fakes."""

from __future__ import annotations

import contextlib
import ctypes
import threading
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

from voicepilot.config import AppConfig
from voicepilot.contracts import (
    AppKind,
    Command,
    ExecutionError,
    Intent,
    ResultStatus,
    WindowContext,
)
from voicepilot.windows import browser, desktop
from voicepilot.windows.context import (
    build_context,
    classify_process,
    none_context,
    process_basename,
    same_target,
)
from voicepilot.windows.executor import WindowsExecutor
from voicepilot.windows.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    HotkeyManager,
    parse_hotkey,
)
from voicepilot.windows.input import (
    INPUT,
    KEYBDINPUT,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    MOUSEINPUT,
    VK_CANCEL_MENU_ACTIVATION,
    VK_LCONTROL,
    VK_LMENU,
    KeyEvent,
    build_key_sequence,
    build_unicode_events,
    chunked,
    release_events,
    vk_for_key,
)
from voicepilot.windows.powerpoint import (
    PowerPointController,
    PowerPointUnavailable,
    match_by_title,
    sanitize_filename,
)

POINTER_SIZE = ctypes.sizeof(ctypes.c_void_p)


# ---------------------------------------------------------------- helpers and fakes


def make_ctx(
    *,
    title: str = "",
    kind: AppKind = AppKind.POWERPOINT,
    browser_name: str | None = None,
    hwnd: int = 42,
) -> WindowContext:
    return WindowContext(
        hwnd=hwnd,
        pid=7,
        process_name="POWERPNT.EXE",
        title=title,
        app_kind=kind,
        browser=browser_name,
    )


class FakeCollection:
    """A 1-based Office collection with Count and Item."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    @property
    def Count(self) -> int:
        return len(self._items)

    def Item(self, index: int) -> Any:
        return self._items[index - 1]


class FakeEditView:
    def __init__(self, index: int) -> None:
        self.Slide = SimpleNamespace(SlideIndex=index)
        self.gotos: list[int] = []

    def GotoSlide(self, index: int) -> None:
        self.gotos.append(index)
        self.Slide.SlideIndex = index


class FakeShowView:
    def __init__(self, position: int, slides: int = 3) -> None:
        self.CurrentShowPosition = position
        self.slides = slides
        self.calls: list[str] = []

    def First(self) -> None:
        self.calls.append("first")
        self.CurrentShowPosition = 1

    def Last(self) -> None:
        self.calls.append("last")
        self.CurrentShowPosition = self.slides

    def Next(self) -> None:
        self.calls.append("next")
        self.CurrentShowPosition += 1

    def Previous(self) -> None:
        self.calls.append("previous")
        self.CurrentShowPosition -= 1

    def GotoSlide(self, index: int) -> None:
        self.calls.append(f"goto {index}")
        self.CurrentShowPosition = index

    def Exit(self) -> None:
        self.calls.append("exit")


class FakeShow:
    def __init__(self, position: int, slides: int = 3) -> None:
        self.View = FakeShowView(position, slides)


class FakeSettings:
    def __init__(self, presentation: FakePresentation) -> None:
        self._presentation = presentation
        self.runs = 0

    def Run(self) -> FakeShow:
        self.runs += 1
        show = FakeShow(1)
        self._presentation.SlideShowWindow = show
        return show


class FakePresentation:
    def __init__(
        self,
        name: str,
        *,
        path: str = "",
        slides: int = 3,
        show_position: int | None = None,
        edit_index: int = 1,
    ) -> None:
        self.Name = name
        self.Path = path
        self.FullName = f"{path}\\{name}" if path else name
        self.Slides = SimpleNamespace(Count=slides)
        self.SlideShowWindow = (
            FakeShow(show_position, slides) if show_position is not None else None
        )
        self.Windows = FakeCollection([SimpleNamespace(View=FakeEditView(edit_index))])
        self.SlideShowSettings = FakeSettings(self)
        self.saves = 0
        self.save_as_calls: list[tuple[str, int]] = []

    def Save(self) -> None:
        self.saves += 1

    def SaveAs(self, path: str, file_format: int) -> None:
        self.save_as_calls.append((path, file_format))


class FakeApp:
    def __init__(self, *presentations: FakePresentation) -> None:
        self.Presentations = FakeCollection(list(presentations))
        self.ActivePresentation = presentations[0]


class FakeContextProvider:
    def __init__(self, still_target: bool = True) -> None:
        self.still_target = still_target

    def capture(self) -> WindowContext:
        raise AssertionError("the executor never captures the context itself")

    def is_still_target(self, ctx: WindowContext) -> bool:
        return self.still_target


class FakeKeys:
    def __init__(self, error: Exception | None = None) -> None:
        self.hotkeys: list[tuple[str, ...]] = []
        self.texts: list[str] = []
        self.error = error

    def send_hotkey(self, *keys: str) -> None:
        if self.error is not None:
            raise self.error
        self.hotkeys.append(keys)

    def type_unicode(self, text: str, *, word: bool = False) -> None:
        if self.error is not None:
            raise self.error
        self.texts.append(text)


@pytest.fixture
def ppt(tmp_path: Path) -> Callable[..., PowerPointController]:
    """Build PowerPoint controllers with a fake application and no COM; shut them down after the test."""
    created: list[PowerPointController] = []

    def make(source: Any, *, save_dir: Path | None = None, timeout_s: float = 5.0) -> PowerPointController:
        def app_source() -> Any:
            if isinstance(source, BaseException):
                raise source
            if callable(source) and not isinstance(source, FakeApp):
                return source()
            return source

        controller = PowerPointController(
            app_source=app_source,
            apartment=contextlib.nullcontext,
            save_dir=save_dir if save_dir is not None else tmp_path / "Docs",
            timeout_s=timeout_s,
        )
        created.append(controller)
        return controller

    yield make
    for controller in created:
        controller.shutdown()


def make_executor(
    *,
    still_target: bool = True,
    keys: FakeKeys | None = None,
    powerpoint: Any = None,
) -> tuple[WindowsExecutor, FakeKeys]:
    key_sender = keys if keys is not None else FakeKeys()
    executor = WindowsExecutor(
        AppConfig(),
        FakeContextProvider(still_target),
        powerpoint=powerpoint,
        keys=key_sender,
    )
    return executor, key_sender


def units_sent(events: list[KeyEvent]) -> list[int]:
    return [
        event.scan
        for event in events
        if event.flags & KEYEVENTF_UNICODE and not event.flags & KEYEVENTF_KEYUP
    ]


# ---------------------------------------------------------------- context


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("POWERPNT.EXE", (AppKind.POWERPOINT, None)),
        ("powerpnt.exe", (AppKind.POWERPOINT, None)),
        ("msedge.exe", (AppKind.BROWSER, "edge")),
        ("MSEDGE.EXE", (AppKind.BROWSER, "edge")),
        ("chrome.exe", (AppKind.BROWSER, "chrome")),
        ("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", (AppKind.BROWSER, "chrome")),
        ("notepad.exe", (AppKind.OTHER, None)),
        ("", (AppKind.NONE, None)),
        ("   ", (AppKind.NONE, None)),
    ],
)
def test_classify_process(name: str, expected: tuple[AppKind, str | None]) -> None:
    assert classify_process(name) == expected


def test_process_basename_accepts_both_separators() -> None:
    assert process_basename("C:\\a\\b\\POWERPNT.EXE") == "POWERPNT.EXE"
    assert process_basename("/opt/app/msedge.exe") == "msedge.exe"
    assert process_basename("chrome.exe") == "chrome.exe"


def test_build_context_classifies_powerpoint_window() -> None:
    ctx = build_context(
        hwnd=10,
        pid=20,
        image_path="C:\\Office\\POWERPNT.EXE",
        title="Báo cáo.pptx - PowerPoint",
        own_pid=99,
    )
    assert ctx.app_kind is AppKind.POWERPOINT
    assert ctx.process_name == "POWERPNT.EXE"
    assert ctx.title == "Báo cáo.pptx - PowerPoint"
    assert ctx.hwnd == 10 and ctx.pid == 20


def test_build_context_ignores_own_process() -> None:
    ctx = build_context(
        hwnd=10, pid=99, image_path="C:\\VoicePilot\\python.exe", title="VoicePilot", own_pid=99
    )
    assert ctx == none_context()
    assert ctx.app_kind is AppKind.NONE


def test_build_context_without_window_or_exe_is_none() -> None:
    assert build_context(hwnd=0, pid=20, image_path="x", title="", own_pid=99).app_kind is AppKind.NONE
    assert build_context(hwnd=10, pid=20, image_path="", title="", own_pid=99).app_kind is AppKind.NONE


def test_same_target_requires_same_hwnd_and_pid() -> None:
    ctx = make_ctx()
    assert same_target(ctx, hwnd=42, pid=7)
    assert not same_target(ctx, hwnd=43, pid=7)
    assert not same_target(ctx, hwnd=42, pid=8)
    assert not same_target(none_context(), hwnd=0, pid=0)


# ---------------------------------------------------------------- hotkeys


@pytest.mark.parametrize(
    ("spec", "expected_mods", "expected_vk"),
    [
        ("ctrl+alt+space", MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x20),
        ("Ctrl + Alt + Space", MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x20),
        ("control+alt+d", MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x44),
        ("ctrl+shift+f12", MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x7B),
        ("win+shift+1", MOD_WIN | MOD_SHIFT | MOD_NOREPEAT, 0x31),
        ("alt+enter", MOD_ALT | MOD_NOREPEAT, 0x0D),
    ],
)
def test_parse_hotkey_valid(spec: str, expected_mods: int, expected_vk: int) -> None:
    assert parse_hotkey(spec) == (expected_mods, expected_vk)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "space",  # no modifier
        "ctrl+alt",  # no key
        "ctrl+alt+xyz",  # unknown key
        "ctrl+ctrl+a",  # duplicate modifier
        "ctrl+a+b",  # two keys
        "ctrl++a",  # empty token
        "ctrl+alt+f25",  # F25 does not exist
    ],
)
def test_parse_hotkey_rejects_invalid(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(spec)


def test_hotkey_manager_register_rejects_bad_spec_without_starting_thread() -> None:
    manager = HotkeyManager()
    with pytest.raises(ValueError):
        manager.register("nonsense", lambda: None)
    manager.register("ctrl+alt+space", lambda: None)  # must not raise


# ---------------------------------------------------------------- input


def test_input_structs_match_win32_layout() -> None:
    if POINTER_SIZE == 8:
        assert ctypes.sizeof(INPUT) == 40
        assert INPUT.u.offset == 8
        assert ctypes.sizeof(KEYBDINPUT) == 24
        assert ctypes.sizeof(MOUSEINPUT) == 32
    else:
        assert ctypes.sizeof(INPUT) == 28
        assert INPUT.u.offset == 4
        assert ctypes.sizeof(KEYBDINPUT) == 16
        assert ctypes.sizeof(MOUSEINPUT) == 24


def test_input_keyboard_fields_reachable_through_anonymous_union() -> None:
    item = INPUT()
    item.type = 1
    item.ki.wVk = 0x11
    item.ki.dwFlags = KEYEVENTF_KEYUP
    assert item.u.ki.wVk == 0x11
    assert item.u.ki.dwFlags == KEYEVENTF_KEYUP


def test_build_unicode_events_vietnamese_text() -> None:
    text = "Tiếng Việt"
    events = build_unicode_events(text)
    assert units_sent(events) == [ord(char) for char in text]
    assert len(events) == 2 * len(text)
    assert all(event.vk == 0 for event in events)


def test_build_unicode_events_uses_surrogate_pair_for_emoji() -> None:
    events = build_unicode_events("😀")
    assert units_sent(events) == [0xD83D, 0xDE00]


def test_build_unicode_events_newline_presses_enter() -> None:
    assert build_unicode_events("\n") == [KeyEvent(0x0D, 0, 0), KeyEvent(0x0D, 0, KEYEVENTF_KEYUP)]


def test_build_unicode_events_crlf_is_single_enter() -> None:
    assert build_unicode_events("\r\n") == build_unicode_events("\n")


def test_build_unicode_events_normalizes_to_nfc() -> None:
    decomposed = "e\u0301"  # e + combining acute accent
    assert units_sent(build_unicode_events(decomposed)) == [0xE9]


def test_build_unicode_events_skips_control_characters() -> None:
    assert units_sent(build_unicode_events("a\x07b")) == [ord("a"), ord("b")]


def test_build_unicode_events_empty_text() -> None:
    assert build_unicode_events("") == []


def test_chunked_splits_in_order() -> None:
    assert [list(batch) for batch in chunked([1, 2, 3, 4, 5], 2)] == [[1, 2], [3, 4], [5]]
    assert list(chunked([], 3)) == []


@pytest.mark.parametrize(
    ("key", "expected"),
    [("ctrl", 0x11), ("tab", 0x09), ("f5", 0x74), ("f24", 0x87), ("a", 0x41), ("7", 0x37), ("space", 0x20)],
)
def test_vk_for_key(key: str, expected: int) -> None:
    assert vk_for_key(key) == expected


@pytest.mark.parametrize("key", ["f25", "xyz", "", "ß"])
def test_vk_for_key_unknown(key: str) -> None:
    assert vk_for_key(key) is None


def test_build_key_sequence_ctrl_tab() -> None:
    assert build_key_sequence(("ctrl", "tab")) == [
        KeyEvent(0x11, 0, 0),
        KeyEvent(0x09, 0, 0),
        KeyEvent(0x09, 0, KEYEVENTF_KEYUP),
        KeyEvent(0x11, 0, KEYEVENTF_KEYUP),
    ]


def test_build_key_sequence_ctrl_shift_tab_releases_in_reverse() -> None:
    sequence = build_key_sequence(("ctrl", "shift", "tab"))
    assert [event.vk for event in sequence] == [0x11, 0x10, 0x09, 0x09, 0x10, 0x11]
    assert [bool(event.flags & KEYEVENTF_KEYUP) for event in sequence] == [False] * 3 + [True] * 3


def test_build_key_sequence_arrow_keys_are_extended() -> None:
    down, up = build_key_sequence(("left",))
    assert down.flags == 0x0001
    assert up.flags == 0x0001 | KEYEVENTF_KEYUP


def test_build_key_sequence_rejects_unknown_key() -> None:
    with pytest.raises(ValueError):
        build_key_sequence(("ctrl", "bogus"))
    with pytest.raises(ValueError):
        build_key_sequence(())


def test_release_events_nothing_held_sends_nothing() -> None:
    assert release_events([]) == []


def test_release_events_releases_only_held_modifiers() -> None:
    events = release_events([VK_LCONTROL, 0x41])  # 0x41 is the letter A, not a modifier
    assert events == [KeyEvent(VK_LCONTROL, 0, KEYEVENTF_KEYUP)]


def test_release_events_cancels_menu_before_releasing_alt() -> None:
    events = release_events([VK_LCONTROL, VK_LMENU])
    assert events[:2] == [
        KeyEvent(VK_CANCEL_MENU_ACTIVATION, 0, 0),
        KeyEvent(VK_CANCEL_MENU_ACTIVATION, 0, KEYEVENTF_KEYUP),
    ]
    assert events[2:] == [KeyEvent(VK_LCONTROL, 0, KEYEVENTF_KEYUP), KeyEvent(VK_LMENU, 0, KEYEVENTF_KEYUP)]


# ---------------------------------------------------------------- PowerPoint helpers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Báo cáo quý 3", "Báo cáo quý 3.pptx"),
        ("  Tên tệp  ", "Tên tệp.pptx"),
        ("slide.pptx", "slide.pptx"),
        ("SLIDE.PPTX", "SLIDE.pptx"),
        ("bài tập.", "bài tập.pptx"),
        ("COMET", "COMET.pptx"),
    ],
)
def test_sanitize_filename_accepts(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "...",
        "a/b",
        "a:b",
        "a?b",
        "a*b",
        'a"b',
        "tab\tname",
        "CON",
        "con.pptx",
        "LPT1",
        "nul",
        "x" * 200,
    ],
)
def test_sanitize_filename_rejects(raw: str) -> None:
    assert sanitize_filename(raw) is None


@pytest.mark.parametrize(
    "raw",
    ["COM¹", "com²", "LPT³ .pptx", "ＣＯＮ", "Nul.txt.pptx", "con .pptx", "COM0", "lpt0"],
)
def test_sanitize_filename_rejects_compatibility_reserved_names(raw: str) -> None:
    assert sanitize_filename(raw) is None


@pytest.mark.parametrize(
    "raw",
    ["Báo\u202ecáo", "Báo\u200bcáo", "Báo\ufeffcáo", "Báo\ue000cáo", "Báo.pptx\u200b", "\u200b", "a\x9fb"],
)
def test_sanitize_filename_rejects_format_private_use_and_control_characters(raw: str) -> None:
    assert sanitize_filename(raw) is None


def test_sanitize_filename_keeps_nfd_vietnamese_as_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Tiếng Việt quan trọng")
    assert sanitize_filename(nfd) == unicodedata.normalize("NFC", "Tiếng Việt quan trọng.pptx")


def test_sanitize_filename_keeps_names_that_only_start_with_a_reserved_word() -> None:
    assert sanitize_filename("CONSOLE¹") == "CONSOLE¹.pptx"


def test_save_as_truncates_stem_without_splitting_combining_marks(
    ppt: Callable[..., PowerPointController], tmp_path: Path
) -> None:
    # Pad to a fixed folder length so a long real tmp_path (Windows) cannot leave no room for the stem.
    folder = tmp_path / ("d" * (150 - len(str(tmp_path)) - 1))
    presentation = FakePresentation("Deck.pptx", path=str(folder))
    # "q" + combining acute has no precomposed form, so NFC keeps it as two code points.
    result = ppt(FakeApp(presentation)).save_as(make_ctx(title="Deck.pptx"), "q\u0301" * 50)
    assert result.status is ResultStatus.OK
    stem = Path(presentation.save_as_calls[0][0]).name[: -len(".pptx")]
    assert 0 < len(stem) < 100
    assert stem == "q\u0301" * (len(stem) // 2)


def test_save_as_keeps_full_path_within_240_even_with_copy_suffix(
    ppt: Callable[..., PowerPointController], tmp_path: Path
) -> None:
    pad = 150 - len(str(tmp_path)) - 1
    if pad < 1:
        pytest.skip("tmp_path already too long to build a 150-character folder")
    folder = tmp_path / ("d" * pad)
    folder.mkdir()
    (folder / ("a" * 120 + ".pptx")).write_bytes(b"")
    presentation = FakePresentation("Deck.pptx", path=str(folder))
    result = ppt(FakeApp(presentation)).save_as(make_ctx(title="Deck.pptx"), "a" * 120)
    assert result.status is ResultStatus.OK
    saved = Path(presentation.save_as_calls[0][0])
    assert len(str(saved)) <= 240


def test_save_as_rejects_when_folder_leaves_no_room(
    ppt: Callable[..., PowerPointController], tmp_path: Path
) -> None:
    presentation = FakePresentation("Deck.pptx", path=str(tmp_path / ("d" * 230)))
    result = ppt(FakeApp(presentation)).save_as(make_ctx(title="Deck.pptx"), "Báo cáo")
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "invalid_filename"
    assert presentation.save_as_calls == []


def test_match_by_title_prefers_longest_name() -> None:
    names = ["Deck.pptx", "Old Deck.pptx"]
    assert match_by_title("Old Deck.pptx - PowerPoint", names) == 1
    assert match_by_title("Deck.pptx - PowerPoint", names) == 0


def test_match_by_title_is_case_insensitive_and_handles_no_match() -> None:
    assert match_by_title("BÁO CÁO.PPTX - PowerPoint", ["báo cáo.pptx"]) == 0
    assert match_by_title("Other - PowerPoint", ["Deck.pptx"]) is None
    assert match_by_title("anything", [""]) is None


# ---------------------------------------------------------------- browser helpers


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com", "https://www.youtube.com"),
        ("  http://example.com/a?b=1 ", "http://example.com/a?b=1"),
        ("javascript:alert(1)", None),
        ("file:///C:/Windows/win.ini", None),
        ("ftp://example.com", None),
        ("https://", None),
        ("https://exa mple.com", None),
        ("", None),
    ],
)
def test_validate_url(url: str, expected: str | None) -> None:
    assert browser.validate_url(url) == expected


def test_build_search_url_encodes_vietnamese_query() -> None:
    query = "thời tiết hôm nay"
    url = browser.build_search_url(query)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.google.com/search"
    assert parse_qs(parts.query) == {"q": [query]}
    assert "%E1%BB%9D" in url  # UTF-8 of U+1EDD, a letter in "thời"
    assert " " not in url


def test_tab_hotkeys_cover_every_tab_intent() -> None:
    assert browser.TAB_HOTKEYS[Intent.BROWSER_PREV_TAB] == ("ctrl", "shift", "tab")
    assert browser.TAB_HOTKEYS[Intent.BROWSER_CLOSE_TAB] == ("ctrl", "w")
    assert set(browser.TAB_HOTKEYS) == set(browser.TAB_MESSAGES)


# ---------------------------------------------------------------- desktop helpers


def test_pick_monitor_index_chooses_largest_overlap() -> None:
    monitors = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
    assert desktop.pick_monitor_index((2000, 100, 2400, 500), monitors) == 1
    # 180 px on the right, 120 on the left
    assert desktop.pick_monitor_index((1800, 0, 2100, 300), monitors) == 1
    # off screen falls back to first
    assert desktop.pick_monitor_index((5000, 5000, 5100, 5100), monitors) == 0


def test_unique_path_adds_counter(tmp_path: Path) -> None:
    assert desktop.unique_path(tmp_path, "Báo cáo.pptx") == tmp_path / "Báo cáo.pptx"
    (tmp_path / "Báo cáo.pptx").write_bytes(b"")
    assert desktop.unique_path(tmp_path, "Báo cáo.pptx") == tmp_path / "Báo cáo (2).pptx"
    (tmp_path / "Báo cáo (2).pptx").write_bytes(b"")
    assert desktop.unique_path(tmp_path, "Báo cáo.pptx") == tmp_path / "Báo cáo (3).pptx"


def test_known_folders_fall_back_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    # On real Windows the Known Folder may be redirected (OneDrive), so force the fallback path.
    monkeypatch.setattr(desktop, "_known_folder", lambda folder_id, fallback: fallback)
    assert desktop.documents_dir() == Path.home() / "Documents"
    assert desktop.pictures_dir() == Path.home() / "Pictures"


# ---------------------------------------------------------------- PowerPoint controller with fake COM


def test_next_slide_in_show_moves_forward(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", show_position=1)
    result = ppt(FakeApp(presentation)).next_slide(make_ctx(title="Deck.pptx - PowerPoint"))
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã chuyển slide tiếp."
    assert presentation.SlideShowWindow.View.calls == ["next"]


def test_next_slide_at_last_slide_is_boundary(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", show_position=3, slides=3)
    result = ppt(FakeApp(presentation)).next_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.BOUNDARY
    assert result.message_vi == "Đã ở slide cuối."
    assert presentation.SlideShowWindow.View.calls == []


def test_previous_slide_at_first_slide_is_boundary_in_edit_view(
    ppt: Callable[..., PowerPointController],
) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", edit_index=1)
    result = ppt(FakeApp(presentation)).previous_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.BOUNDARY
    assert result.message_vi == "Đã ở slide đầu."
    assert presentation.Windows.Item(1).View.gotos == []


def test_next_slide_in_edit_view_goes_to_next_index(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", edit_index=2, slides=3)
    result = ppt(FakeApp(presentation)).next_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert presentation.Windows.Item(1).View.gotos == [3]


def test_first_slide_in_show_goes_to_slide_one(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", show_position=3, slides=3)
    result = ppt(FakeApp(presentation)).first_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã về slide đầu tiên."
    assert presentation.SlideShowWindow.View.calls == ["first"]


def test_last_slide_in_show_goes_to_last_slide(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", show_position=1, slides=4)
    result = ppt(FakeApp(presentation)).last_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã đến slide cuối cùng."
    assert presentation.SlideShowWindow.View.calls == ["last"]


def test_first_slide_in_edit_view_goes_to_index_one(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", edit_index=2, slides=3)
    result = ppt(FakeApp(presentation)).first_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã về slide đầu tiên."
    assert presentation.Windows.Item(1).View.gotos == [1]


def test_last_slide_in_edit_view_goes_to_slide_count(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", edit_index=1, slides=5)
    result = ppt(FakeApp(presentation)).last_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã đến slide cuối cùng."
    assert presentation.Windows.Item(1).View.gotos == [5]


def test_first_and_last_when_already_there_still_succeed(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", edit_index=1, slides=3)
    controller = ppt(FakeApp(presentation))
    assert controller.first_slide(make_ctx(title="Deck.pptx")).status is ResultStatus.OK
    assert presentation.Windows.Item(1).View.gotos == [1]


def test_first_and_last_on_empty_presentation_are_boundary(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", edit_index=1, slides=0)
    controller = ppt(FakeApp(presentation))
    for action in (controller.first_slide, controller.last_slide):
        result = action(make_ctx(title="Deck.pptx"))
        assert result.status is ResultStatus.BOUNDARY
        assert result.error_code == "no_slides"
    assert presentation.Windows.Item(1).View.gotos == []


def test_title_selects_presentation_not_active_one(ppt: Callable[..., PowerPointController]) -> None:
    first = FakePresentation("A.pptx", path="C:\\Work", edit_index=1)
    second = FakePresentation("B.pptx", path="C:\\Work", edit_index=1)
    ppt(FakeApp(first, second)).next_slide(make_ctx(title="B.pptx - PowerPoint"))
    assert second.Windows.Item(1).View.gotos == [2]
    assert first.Windows.Item(1).View.gotos == []


def test_running_show_is_used_when_title_matches_nothing(ppt: Callable[..., PowerPointController]) -> None:
    idle = FakePresentation("A.pptx", path="C:\\Work")
    showing = FakePresentation("B.pptx", path="C:\\Work", show_position=1)
    result = ppt(FakeApp(idle, showing)).next_slide(make_ctx(title="Something else"))
    assert result.status is ResultStatus.OK
    assert showing.SlideShowWindow.View.calls == ["next"]


def test_start_show_runs_from_first_slide(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work")
    result = ppt(FakeApp(presentation)).start_show(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert presentation.SlideShowSettings.runs == 1
    assert presentation.SlideShowWindow.View.calls == ["goto 1"]


def test_stop_show_exits_running_show(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", show_position=2)
    result = ppt(FakeApp(presentation)).stop_show(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert presentation.SlideShowWindow.View.calls == ["exit"]


def test_stop_show_without_show_reports_nothing_running(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work")
    result = ppt(FakeApp(presentation)).stop_show(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Không có trình chiếu nào đang chạy."


def test_save_unsaved_presentation_asks_for_name(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Trình bày1", path="")
    result = ppt(FakeApp(presentation)).save(make_ctx(title="Trình bày1 - PowerPoint"))
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "unsaved"
    assert "lưu thành tên tệp" in result.message_vi
    assert presentation.saves == 0


def test_save_saved_presentation(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work")
    result = ppt(FakeApp(presentation)).save(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.OK
    assert presentation.saves == 1
    assert result.data["path"] == "C:\\Work\\Deck.pptx"


def test_save_as_duplicate_name_gets_counter(
    ppt: Callable[..., PowerPointController], tmp_path: Path
) -> None:
    (tmp_path / "Báo cáo.pptx").write_bytes(b"")
    presentation = FakePresentation("Báo cáo.pptx", path=str(tmp_path))
    result = ppt(FakeApp(presentation)).save_as(make_ctx(title="Báo cáo.pptx - PowerPoint"), "Báo cáo")
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã lưu thành Báo cáo (2).pptx"
    saved_path, file_format = presentation.save_as_calls[0]
    assert Path(saved_path).name == "Báo cáo (2).pptx"
    assert file_format == 24


def test_save_as_unsaved_presentation_uses_default_folder(
    ppt: Callable[..., PowerPointController], tmp_path: Path
) -> None:
    presentation = FakePresentation("Ghi chú1", path="")
    controller = ppt(FakeApp(presentation), save_dir=tmp_path / "Docs")
    result = controller.save_as(make_ctx(title="Ghi chú1"), "Ghi chú")
    assert result.status is ResultStatus.OK
    assert Path(presentation.save_as_calls[0][0]) == tmp_path / "Docs" / "Ghi chú.pptx"
    assert (tmp_path / "Docs").is_dir()


def test_save_as_invalid_name_is_rejected_without_saving(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work")
    result = ppt(FakeApp(presentation)).save_as(make_ctx(title="Deck.pptx"), "CON")
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "invalid_filename"
    assert presentation.save_as_calls == []


def test_powerpoint_not_running_is_not_available(ppt: Callable[..., PowerPointController]) -> None:
    controller = ppt(PowerPointUnavailable("PowerPoint chưa mở.", "ppt_not_running"))
    result = controller.next_slide(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.message_vi == "PowerPoint chưa mở."


def test_powerpoint_without_presentation_is_not_available(ppt: Callable[..., PowerPointController]) -> None:
    empty = SimpleNamespace(Presentations=FakeCollection([]), ActivePresentation=None)
    result = ppt(empty).save(make_ctx(title=""))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.error_code == "ppt_no_document"


def test_powerpoint_timeout_is_reported(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work")

    def slow_source() -> FakeApp:
        time.sleep(0.5)
        return FakeApp(presentation)

    result = ppt(slow_source, timeout_s=0.05).save(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.ERROR
    assert result.message_vi == "PowerPoint không phản hồi."
    assert result.error_code == "ppt_timeout"


def test_powerpoint_unexpected_error_is_reported_not_raised(ppt: Callable[..., PowerPointController]) -> None:
    controller = ppt(RuntimeError("COM exploded"))
    result = controller.start_show(make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "ppt_error"


# ---------------------------------------------------------------- executor dispatch


@pytest.mark.parametrize("intent", [intent for intent in Intent if intent is not Intent.UNKNOWN])
def test_cancelled_command_never_reaches_a_windows_backend(intent, monkeypatch):
    cancel = threading.Event()
    cancel.set()
    powerpoint = MagicMock()
    executor, keys = make_executor(powerpoint=powerpoint)
    launch, screenshot, url = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(desktop, "open_app", launch)
    monkeypatch.setattr(desktop, "screenshot", screenshot)
    monkeypatch.setattr(browser, "launch_url", url)
    params = {"filename": "Test", "text": "Xin chào", "url": "https://example.com",
              "query": "Test", "app": "notepad.exe"}
    result = executor.execute(Command(intent, "Synthetic", params=params), make_ctx(), cancel=cancel)
    assert result.status is ResultStatus.NEEDS_RETRY and result.error_code == "cancelled"
    assert not any(mock.mock_calls for mock in (powerpoint, launch, screenshot, url))
    assert keys.hotkeys == [] and keys.texts == []


def test_cancel_during_context_check_prevents_sendinput():
    cancel = threading.Event()
    def still_target(_ctx):
        cancel.set()
        return True
    keys = FakeKeys()
    executor = WindowsExecutor(AppConfig(), SimpleNamespace(is_still_target=still_target),
                               powerpoint=MagicMock(), keys=keys)
    result = executor.execute(Command(Intent.BROWSER_CLOSE_TAB, "đóng tab"),
                              make_ctx(kind=AppKind.BROWSER, browser_name="edge"), cancel=cancel)
    assert result.status is ResultStatus.NEEDS_RETRY and result.error_code == "cancelled"
    assert keys.hotkeys == []


def test_powerpoint_cancellation_is_per_turn_and_skips_cancelled_queue(ppt, tmp_path):
    presentation = FakePresentation("Test.pptx", path=str(tmp_path))
    app = FakeApp(presentation)
    gate, entered = threading.Event(), threading.Event()
    calls = []
    def source():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            assert gate.wait(3)
        return app
    controller = ppt(source, timeout_s=3)
    ctx = make_ctx(title="Test.pptx")
    first_cancel, second_cancel = threading.Event(), threading.Event()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        first = pool.submit(controller.save, ctx, cancel=first_cancel)
        assert entered.wait(1)
        first_cancel.set()
        assert first.result(timeout=1).error_code == "ppt_cancelled"
        second = pool.submit(controller.save, ctx, cancel=second_cancel)
        deadline = time.monotonic() + 1
        while controller._jobs.qsize() != 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert controller._jobs.qsize() == 1
        second_cancel.set()
        assert second.result(timeout=1).error_code == "ppt_cancelled"
        third = pool.submit(controller.save, ctx)
        assert presentation.saves == 0
        gate.set()
        assert third.result(timeout=1).status is ResultStatus.OK
        assert presentation.saves == 1 and len(calls) == 2
    finally:
        gate.set()
        pool.shutdown(wait=True)


def test_target_changed_returns_needs_retry_and_sends_nothing() -> None:
    powerpoint = MagicMock()
    executor, keys = make_executor(still_target=False, powerpoint=powerpoint)
    result = executor.execute(Command(Intent.PPT_NEXT_SLIDE, "slide tiếp"), make_ctx())
    assert result.status is ResultStatus.NEEDS_RETRY
    assert result.message_vi == "Cửa sổ đích đã thay đổi, hãy thử lại."
    assert result.error_code == "target_changed"
    assert powerpoint.method_calls == []
    assert keys.hotkeys == [] and keys.texts == []


def test_strict_command_without_window_is_not_available() -> None:
    # Uses a PowerPoint-kind context with hwnd=0: a NONE context now fails the app-kind gate first
    # (see the no_powerpoint_window tests), so this is the only way to reach the no_target path.
    powerpoint = MagicMock()
    executor, _ = make_executor(powerpoint=powerpoint)
    result = executor.execute(Command(Intent.PPT_SAVE, "lưu"), make_ctx(hwnd=0))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.error_code == "no_target"
    assert powerpoint.method_calls == []


PPT_INTENTS_WITH_PARAMS = [
    (Intent.PPT_SAVE, {}),
    (Intent.PPT_SAVE_AS, {"filename": "Báo cáo"}),
    (Intent.PPT_NEXT_SLIDE, {}),
    (Intent.PPT_PREV_SLIDE, {}),
    (Intent.PPT_FIRST_SLIDE, {}),
    (Intent.PPT_LAST_SLIDE, {}),
    (Intent.PPT_START_SHOW, {}),
    (Intent.PPT_STOP_SHOW, {}),
]


@pytest.mark.parametrize(("intent", "params"), PPT_INTENTS_WITH_PARAMS)
@pytest.mark.parametrize(
    "ctx",
    [
        make_ctx(kind=AppKind.BROWSER, browser_name="chrome", title="Google Chrome"),
        make_ctx(kind=AppKind.OTHER, title="Notepad"),
        none_context(),
    ],
    ids=["browser", "other", "none"],
)
def test_ppt_intent_outside_powerpoint_is_refused_without_touching_com(
    intent: Intent, params: dict[str, str], ctx: WindowContext
) -> None:
    powerpoint = MagicMock()
    executor, keys = make_executor(powerpoint=powerpoint)
    result = executor.execute(Command(intent, "lưu", params=params), ctx)
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.message_vi == "Hãy chuyển sang cửa sổ PowerPoint rồi thử lại."
    assert result.error_code == "no_powerpoint_window"
    assert powerpoint.method_calls == []
    assert keys.hotkeys == [] and keys.texts == []


def test_ppt_save_in_chrome_does_not_change_background_deck(ppt: Callable[..., PowerPointController]) -> None:
    # Regression: with Chrome focused, ActivePresentation used to be saved silently.
    deck = FakePresentation("Deck.pptx", path="C:\\Work", show_position=1)
    controller = ppt(FakeApp(deck))
    executor, _ = make_executor(powerpoint=controller)
    result = executor.execute(
        Command(Intent.PPT_NEXT_SLIDE, "slide tiếp"),
        make_ctx(kind=AppKind.BROWSER, browser_name="chrome", title="Deck.pptx - Google Chrome"),
    )
    assert result.error_code == "no_powerpoint_window"
    assert deck.saves == 0
    assert deck.SlideShowWindow.View.calls == []


def test_powerpoint_missing_is_not_available_through_executor(
    ppt: Callable[..., PowerPointController],
) -> None:
    controller = ppt(PowerPointUnavailable("PowerPoint chưa mở.", "ppt_not_running"))
    executor, _ = make_executor(powerpoint=controller)
    result = executor.execute(Command(Intent.PPT_NEXT_SLIDE, "slide tiếp"), make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.message_vi == "PowerPoint chưa mở."


def test_boundary_passes_through_executor(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Deck.pptx", path="C:\\Work", show_position=3, slides=3)
    executor, _ = make_executor(powerpoint=ppt(FakeApp(presentation)))
    result = executor.execute(Command(Intent.PPT_NEXT_SLIDE, "slide tiếp"), make_ctx(title="Deck.pptx"))
    assert result.status is ResultStatus.BOUNDARY


def test_unsaved_save_passes_through_executor(ppt: Callable[..., PowerPointController]) -> None:
    presentation = FakePresentation("Trình bày1", path="")
    executor, _ = make_executor(powerpoint=ppt(FakeApp(presentation)))
    result = executor.execute(Command(Intent.PPT_SAVE, "lưu"), make_ctx(title="Trình bày1"))
    assert result.status is ResultStatus.INVALID_PARAM


def test_browser_tab_with_non_browser_context_is_not_available() -> None:
    executor, keys = make_executor()
    result = executor.execute(Command(Intent.BROWSER_NEXT_TAB, "tab tiếp"), make_ctx(kind=AppKind.POWERPOINT))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.message_vi == "Chưa có cửa sổ trình duyệt."
    assert keys.hotkeys == []


def test_browser_previous_tab_sends_ctrl_shift_tab() -> None:
    executor, keys = make_executor()
    ctx = make_ctx(kind=AppKind.BROWSER, browser_name="edge")
    result = executor.execute(Command(Intent.BROWSER_PREV_TAB, "tab trước"), ctx)
    assert result.status is ResultStatus.OK
    assert keys.hotkeys == [("ctrl", "shift", "tab")]


def test_browser_tab_is_blocked_when_target_changed() -> None:
    executor, keys = make_executor(still_target=False)
    ctx = make_ctx(kind=AppKind.BROWSER, browser_name="chrome")
    result = executor.execute(Command(Intent.BROWSER_CLOSE_TAB, "đóng tab"), ctx)
    assert result.status is ResultStatus.NEEDS_RETRY
    assert keys.hotkeys == []


def test_open_site_rejects_javascript_url() -> None:
    executor, _ = make_executor()
    command = Command(Intent.BROWSER_OPEN_SITE, "mở", params={"url": "javascript:alert(1)", "site": "x"})
    result = executor.execute(command, make_ctx(kind=AppKind.OTHER))
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "invalid_url"


def test_open_site_launches_validated_url(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[tuple[str, WindowContext]] = []
    monkeypatch.setattr(browser, "launch_url", lambda url, ctx: launched.append((url, ctx)))
    executor, _ = make_executor()
    ctx = make_ctx(kind=AppKind.BROWSER, browser_name="edge")
    command = Command(
        Intent.BROWSER_OPEN_SITE, "youtube", params={"url": "https://www.youtube.com", "site": "youtube"}
    )
    result = executor.execute(command, ctx)
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đang mở youtube."
    assert launched == [("https://www.youtube.com", ctx)]


def test_search_builds_google_url_with_vietnamese_query(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[str] = []
    monkeypatch.setattr(browser, "launch_url", lambda url, ctx: launched.append(url))
    executor, _ = make_executor()
    query = "thời tiết hôm nay"
    result = executor.execute(
        Command(Intent.BROWSER_SEARCH, "tìm", params={"query": query}), make_ctx(kind=AppKind.BROWSER)
    )
    assert result.status is ResultStatus.OK
    assert parse_qs(urlsplit(launched[0]).query) == {"q": [query]}


def test_search_without_query_is_invalid() -> None:
    executor, _ = make_executor()
    result = executor.execute(Command(Intent.BROWSER_SEARCH, "tìm", params={"query": "   "}), make_ctx())
    assert result.status is ResultStatus.INVALID_PARAM


def test_launch_failure_is_error_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(url: str, ctx: WindowContext) -> None:
        raise ExecutionError("no browser")

    monkeypatch.setattr(browser, "launch_url", fail)
    executor, _ = make_executor()
    command = Command(Intent.BROWSER_OPEN_SITE, "x", params={"url": "https://example.com", "site": "x"})
    result = executor.execute(command, make_ctx(kind=AppKind.OTHER))
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "launch_failed"


def test_open_app_does_not_check_target(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(desktop, "open_app", lambda target: opened.append(target))
    executor, _ = make_executor(still_target=False)
    command = Command(Intent.WIN_OPEN_APP, "mở word", params={"app": "WINWORD.EXE", "app_name": "word"})
    result = executor.execute(command, make_ctx())
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đang mở word."
    assert opened == ["WINWORD.EXE"]


def test_open_app_failure_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(target: str) -> None:
        raise ExecutionError("missing")

    monkeypatch.setattr(desktop, "open_app", fail)
    executor, _ = make_executor()
    command = Command(Intent.WIN_OPEN_APP, "mở word", params={"app": "WINWORD.EXE", "app_name": "Word"})
    result = executor.execute(command, make_ctx())
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.message_vi == "Không mở được Word."


def test_screenshot_with_changed_target_takes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    taken = MagicMock()
    monkeypatch.setattr(desktop, "screenshot", taken)
    executor, _ = make_executor(still_target=False)
    result = executor.execute(Command(Intent.WIN_SCREENSHOT, "chụp màn hình"), make_ctx())
    assert result.status is ResultStatus.NEEDS_RETRY
    taken.assert_not_called()


def test_screenshot_returns_saved_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    saved = tmp_path / "VoicePilot_20260101_120000.png"
    monkeypatch.setattr(desktop, "screenshot", lambda ctx, hide_overlay=None: saved)
    executor, _ = make_executor()
    result = executor.execute(Command(Intent.WIN_SCREENSHOT, "chụp màn hình"), make_ctx())
    assert result.status is ResultStatus.OK
    assert result.data == {"path": str(saved)}


def test_dictation_types_text_after_target_check() -> None:
    executor, keys = make_executor()
    command = Command(Intent.DICTATE_TEXT, "Xin chào", params={"text": "Xin chào\nTạm biệt"})
    result = executor.execute(command, make_ctx())
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã chèn văn bản."
    assert keys.texts == ["Xin chào\nTạm biệt"]


def test_dictation_blank_text_is_invalid() -> None:
    executor, keys = make_executor()
    result = executor.execute(Command(Intent.DICTATE_TEXT, " ", params={"text": "   "}), make_ctx())
    assert result.status is ResultStatus.INVALID_PARAM
    assert keys.texts == []


def test_dictation_send_failure_is_reported() -> None:
    executor, _ = make_executor(keys=FakeKeys(error=ExecutionError("UIPI")))
    result = executor.execute(Command(Intent.DICTATE_TEXT, "abc", params={"text": "abc"}), make_ctx())
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "send_input_failed"


def test_unknown_command_suggests_example() -> None:
    executor, _ = make_executor()
    result = executor.execute(Command(Intent.UNKNOWN, "blah", example="Slide tiếp theo"), make_ctx())
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "unknown_command"
    assert "Slide tiếp theo" in result.message_vi


def test_missing_parameter_names_it_and_suggests_example() -> None:
    executor, _ = make_executor()
    command = Command(Intent.BROWSER_SEARCH, "tìm", missing=("query",), example="Tìm kiếm thời tiết")
    result = executor.execute(command, make_ctx(kind=AppKind.BROWSER))
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "missing_param"
    assert "query" in result.message_vi and "Tìm kiếm thời tiết" in result.message_vi


def test_backend_exception_becomes_error_result() -> None:
    executor, _ = make_executor(keys=FakeKeys(error=RuntimeError("boom")))
    ctx = make_ctx(kind=AppKind.BROWSER, browser_name="edge")
    result = executor.execute(Command(Intent.BROWSER_NEW_TAB, "tab mới"), ctx)
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "exception"


def test_executor_shutdown_stops_powerpoint_worker() -> None:
    powerpoint = MagicMock()
    executor, _ = make_executor(powerpoint=powerpoint)
    executor.shutdown()
    powerpoint.shutdown.assert_called_once_with()


@pytest.mark.parametrize("text", ["f👋b", "Xin chào Việt Nam 👋 — Word.", "😀👋𝄞"])
def test_word_dictation_preserves_non_bmp_characters_with_sendinput(monkeypatch, text):
    from dataclasses import replace

    from voicepilot.config import load_config
    from voicepilot.windows import input as ops
    from voicepilot.windows.executor import WindowsExecutor

    # Reproduce Word's surrogate handling and selected Alt+X replacement behavior.
    content = "Existing ff"
    caret = anchor = len(content)
    shift = alt = False
    def consume(events):
        nonlocal content, caret, anchor, shift, alt
        for event in events:
            if event.vk == ops.VK_SHIFT:
                shift = not bool(event.flags & ops.KEYEVENTF_KEYUP)
            elif event.vk == ops.VK_MENU:
                alt = not bool(event.flags & ops.KEYEVENTF_KEYUP)
            elif event.flags & ops.KEYEVENTF_KEYUP:
                continue
            elif event.flags & ops.KEYEVENTF_UNICODE:
                # This Office build turns isolated surrogate packets into digits.
                char = "1" if 0xD800 <= event.scan <= 0xDBFF else (
                    "2" if 0xDC00 <= event.scan <= 0xDFFF else chr(event.scan))
                low, high = sorted((caret, anchor))
                content = content[:low] + char + content[high:]
                caret = anchor = low + len(char)
            elif event.vk == ops.VK_LEFT and shift:
                caret -= 1
            elif event.vk == ord("X") and alt:
                low, high = sorted((caret, anchor))
                content = content[:low] + chr(int(content[low:high], 16)) + content[high:]
                caret, anchor = low, low + 1  # Word leaves the converted glyph selected.
            elif event.vk == ops.VK_RIGHT:
                caret = anchor = max(caret, anchor)
    monkeypatch.setattr(ops, "release_modifiers", lambda: None)
    monkeypatch.setattr(ops, "_send_events", consume)
    provider = MagicMock(is_still_target=MagicMock(return_value=True))
    executor = WindowsExecutor(load_config(Path("unused-verification-config.toml")), provider,
                               powerpoint=MagicMock())
    ctx = replace(make_ctx(), process_name="WINWORD.EXE")
    result = executor.execute(Command(Intent.DICTATE_TEXT, text, params={"text": text}), ctx)
    assert result.status is ResultStatus.OK
    assert content == "Existing ff" + text
    assert caret == anchor == len(content)
