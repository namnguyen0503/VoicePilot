"""Black-box QA tests for the Windows layer's pure parts and injected backends.

Win32, COM and mss are never touched: PowerPoint is a fake object model, keys and browser
launches are fakes, and the Windows-only calls are monkeypatched where a handler reaches them.
"""

from __future__ import annotations

import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from voicepilot.config import AppConfig
from voicepilot.contracts import (
    ActionResult,
    AppKind,
    Command,
    ExecutionError,
    Intent,
    ResultStatus,
    WindowContext,
)
from voicepilot.windows import browser, desktop
from voicepilot.windows import executor as executor_mod
from voicepilot.windows import input as keys_mod
from voicepilot.windows import powerpoint as ppt
from voicepilot.windows.context import build_context, classify_process, same_target
from voicepilot.windows.hotkeys import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, parse_hotkey

# --- sanitize_filename (PLAN: "Giữ tên tiếng Việt, dùng .pptx ... Tên không hợp lệ thì yêu cầu nói lại") ---


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("Báo cáo tháng 10", "Báo cáo tháng 10.pptx"),
        ("Báo cáo.pptx", "Báo cáo.pptx"),
        ("Báo cáo.PPTX", "Báo cáo.pptx"),
        ("Báo cáo...", "Báo cáo.pptx"),
        ("   Báo cáo   ", "Báo cáo.pptx"),
        ("Báo cáo Q3.v2", "Báo cáo Q3.v2.pptx"),
        ("CONSOLE", "CONSOLE.pptx"),
        ("con1", "con1.pptx"),
        ("a" * 120, "a" * 120 + ".pptx"),
    ],
)
def test_sanitize_accepts_and_normalizes(spoken, expected):
    assert ppt.sanitize_filename(spoken) == expected


def test_sanitize_nfd_name_is_stored_as_nfc():
    nfd = unicodedata.normalize("NFD", "Báo cáo")
    result = ppt.sanitize_filename(nfd)
    assert result == unicodedata.normalize("NFC", "Báo cáo.pptx")


@pytest.mark.parametrize("spoken", ["", "   ", ".", "..", "...", ".pptx", " .pptx "])
def test_sanitize_rejects_empty_like_names(spoken):
    assert ppt.sanitize_filename(spoken) is None


def test_sanitize_length_limit_is_enforced():
    assert ppt.sanitize_filename("a" * 121) is None


@pytest.mark.parametrize(
    "spoken",
    [
        "../../Windows/system32/x",
        "a/b",
        "a\\b",
        "C:\\temp\\x",
        "a:b",
        "a*b",
        "a?b",
        'a"b',
        "a<b",
        "a>b",
        "a|b",
        "a\x01b",
        "a\x7fb",
        "a\nb",
    ],
)
def test_sanitize_rejects_path_and_forbidden_characters(spoken):
    assert ppt.sanitize_filename(spoken) is None


@pytest.mark.parametrize(
    "spoken",
    ["CON", "con", "CON.pptx", "nul", "AUX ", "PRN.txt", "COM1", "com9.pptx", "LPT1", "lpt9 .pptx", "CON."],
)
def test_sanitize_rejects_reserved_device_names_even_with_extension(spoken):
    assert ppt.sanitize_filename(spoken) is None


def test_sanitize_rejects_superscript_com_names():
    assert ppt.sanitize_filename("COM¹") is None


def test_sanitize_rejects_invisible_format_characters():
    assert ppt.sanitize_filename("Báo\u202ecáo") is None
    assert ppt.sanitize_filename("Báo\u200bcáo") is None


# --- unique_path (PLAN: "trùng tên tự thêm (2), (3)…") ---------------------------------------


def test_unique_path_returns_plain_name_when_free(tmp_path):
    assert desktop.unique_path(tmp_path, "Báo cáo.pptx") == tmp_path / "Báo cáo.pptx"


def test_unique_path_adds_numbers_for_collisions(tmp_path):
    (tmp_path / "Báo cáo.pptx").write_bytes(b"")
    assert desktop.unique_path(tmp_path, "Báo cáo.pptx") == tmp_path / "Báo cáo (2).pptx"
    (tmp_path / "Báo cáo (2).pptx").write_bytes(b"")
    assert desktop.unique_path(tmp_path, "Báo cáo.pptx") == tmp_path / "Báo cáo (3).pptx"


def test_unique_path_keeps_dots_in_stem(tmp_path):
    (tmp_path / "v1.2.pptx").write_bytes(b"")
    assert desktop.unique_path(tmp_path, "v1.2.pptx") == tmp_path / "v1.2 (2).pptx"


# --- validate_url and build_search_url (PLAN: "Website nhận tên alias ... hoặc địa chỉ HTTP/HTTPS") ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com",
        "http://localhost:8080/x",
        "HTTPS://EXAMPLE.COM",
        "https://例子.测试/路径",
        "https://example.com/a?b=1&c=2#frag",
    ],
)
def test_validate_url_accepts_http_urls(url):
    assert browser.validate_url(url) == url


def test_validate_url_strips_surrounding_space():
    assert browser.validate_url("  https://example.com  ") == "https://example.com"
    assert browser.validate_url("https://example.com\n") == "https://example.com"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "vbscript:msgbox",
        "data:text/html,<b>x</b>",
        "file:///C:/Windows",
        "ftp://example.com",
        "https://",
        "https:///path",
        "https:example.com",
        "//example.com",
        "example.com",
        "https://exa mple.com",
        "https://\u00a0example.com",
        "https://exam\nple.com",
        "https://ex\x00ample.com",
        "https://[::1",
    ],
)
def test_validate_url_rejects_non_http_and_malformed(url):
    assert browser.validate_url(url) is None


@pytest.mark.parametrize("query", ["giá vàng", "C++ & Java", "100% gốc #1", "a+b", "x=1&y=2", "tab\tkey"])
def test_search_url_round_trips_query_exactly(query):
    url = browser.build_search_url(query)
    assert url.startswith(browser.SEARCH_ENDPOINT + "?")
    qs = parse_qs(urlsplit(url).query, keep_blank_values=True)
    assert qs["q"] == [query.strip()]


def test_search_url_has_no_raw_reserved_characters_in_query_part():
    parts = urlsplit(browser.build_search_url("a&b#c+d%e"))
    assert parts.fragment == ""
    assert "&" not in parts.query and "#" not in parts.query


def test_search_url_strips_query_edges():
    assert parse_qs(urlsplit(browser.build_search_url("  giá vàng  ")).query)["q"] == ["giá vàng"]


# --- hotkeys.parse_hotkey (PLAN: default Ctrl+Alt+Space, Ctrl+Alt+D) -----------------------


def test_default_hotkeys_parse():
    assert parse_hotkey("ctrl+alt+space") == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x20)
    assert parse_hotkey("ctrl+alt+d") == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("D"))


def test_hotkey_is_case_and_space_insensitive():
    assert parse_hotkey(" Ctrl + Shift + F5 ") == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x74)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "space",
        "ctrl",
        "ctrl+",
        "+a",
        "ctrl+ctrl+a",
        "ctrl+a+b",
        "ctrl+alt+f25",
        "ctrl+alt+%",
        "ctrl+alt+ ",
    ],
)
def test_bad_hotkeys_are_rejected(spec):
    with pytest.raises(ValueError):
        parse_hotkey(spec)


# --- input builders ----------------------------------------------------------------------


def _typed(events) -> str:
    units = [
        e.scan
        for e in events
        if e.flags & keys_mod.KEYEVENTF_UNICODE and not e.flags & keys_mod.KEYEVENTF_KEYUP
    ]
    return bytes(b for u in units for b in u.to_bytes(2, "little")).decode("utf-16-le")


@pytest.mark.parametrize(
    "text",
    ["Đã lưu", "tiếng Việt: ệ ừ ỗ", "emoji 🎉 ok", "ascii & symbols % # \\ /", "中文 テスト"],
)
def test_unicode_events_reproduce_text_exactly(text):
    assert _typed(keys_mod.build_unicode_events(text)) == text


def test_unicode_events_normalize_to_nfc_before_typing():
    decomposed = unicodedata.normalize("NFD", "ệ")
    assert _typed(keys_mod.build_unicode_events(decomposed)) == "ệ"


def test_unicode_events_map_newline_tab_and_drop_control_chars():
    events = keys_mod.build_unicode_events("a\nb\tc\rd\x01e\x7f")
    assert _typed(events) == "abcde"
    vks = [e.vk for e in events if e.flags & keys_mod.KEYEVENTF_UNICODE == 0]
    assert vks.count(keys_mod.VK_RETURN) == 2  # press + release for "\n"
    assert vks.count(keys_mod.VK_TAB) == 2


def test_unicode_events_empty_text_produces_nothing():
    assert keys_mod.build_unicode_events("") == []


def test_key_sequence_presses_in_order_and_releases_in_reverse():
    events = keys_mod.build_key_sequence(("ctrl", "shift", "tab"))
    codes = [(e.vk, bool(e.flags & keys_mod.KEYEVENTF_KEYUP)) for e in events]
    assert codes == [
        (keys_mod.VK_CONTROL, False),
        (keys_mod.VK_SHIFT, False),
        (keys_mod.VK_TAB, False),
        (keys_mod.VK_TAB, True),
        (keys_mod.VK_SHIFT, True),
        (keys_mod.VK_CONTROL, True),
    ]


@pytest.mark.parametrize("keys", [(), ("ctrl", "notakey")])
def test_key_sequence_rejects_bad_input(keys):
    with pytest.raises(ValueError):
        keys_mod.build_key_sequence(keys)


def test_release_events_cancel_menu_before_alt_release():
    events = keys_mod.release_events([keys_mod.VK_LMENU, keys_mod.VK_LCONTROL])
    assert events[0].vk == keys_mod.VK_CANCEL_MENU_ACTIVATION
    released = [e.vk for e in events if e.flags & keys_mod.KEYEVENTF_KEYUP]
    assert set(released) >= {keys_mod.VK_LMENU, keys_mod.VK_LCONTROL}


def test_release_events_empty_when_nothing_held():
    assert keys_mod.release_events([]) == []
    assert keys_mod.release_events([0x41]) == []  # a letter is not a modifier to release


# --- context classification (PLAN: PowerPoint/Edge/Chrome detection) ----------------------


@pytest.mark.parametrize(
    "image,kind,browser_name",
    [
        ("C:\\Program Files\\Microsoft Office\\root\\Office16\\POWERPNT.EXE", AppKind.POWERPOINT, None),
        ("powerpnt.exe", AppKind.POWERPOINT, None),
        ("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", AppKind.BROWSER, "edge"),
        ("chrome.EXE", AppKind.BROWSER, "chrome"),
        ("C:\\x\\firefox.exe", AppKind.OTHER, None),
        ("powerpnt.exe.bak", AppKind.OTHER, None),
        ("", AppKind.NONE, None),
        ("C:\\x\\", AppKind.NONE, None),
    ],
)
def test_process_classification(image, kind, browser_name):
    assert classify_process(image) == (kind, browser_name)


def test_own_process_and_missing_handles_give_none_context():
    own = build_context(hwnd=5, pid=77, image_path="x\\VoicePilot.exe", title="t", own_pid=77)
    assert own.app_kind is AppKind.NONE and own.hwnd == 0
    no_hwnd = build_context(hwnd=0, pid=7, image_path="x\\chrome.exe", title="", own_pid=1)
    assert no_hwnd.app_kind is AppKind.NONE
    unreadable = build_context(hwnd=5, pid=7, image_path="", title="admin", own_pid=1)
    assert unreadable.app_kind is AppKind.NONE


def test_same_target_requires_matching_hwnd_and_pid():
    ctx = WindowContext(
        hwnd=5, pid=7, process_name="chrome.exe", title="", app_kind=AppKind.BROWSER, browser="chrome"
    )
    assert same_target(ctx, 5, 7)
    assert not same_target(ctx, 5, 8)
    assert not same_target(ctx, 6, 7)
    assert not same_target(WindowContext(0, 0, "", "", AppKind.NONE, None), 0, 0)


# --- PowerPoint pure logic with a fake object model ------------------------------------


class Coll:
    """Office-style 1-based collection."""

    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        return self._items[index - 1]

    def __iter__(self):
        return iter(self._items)


class Obj:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)


class FakeView:
    def __init__(self, index=1):
        self.Slide = Obj(SlideIndex=index)
        self.calls: list[tuple] = []

    def GotoSlide(self, i):
        self.calls.append(("goto", i))
        self.Slide.SlideIndex = i

    def Next(self):
        self.calls.append(("next",))

    def Previous(self):
        self.calls.append(("previous",))

    def Exit(self):
        self.calls.append(("exit",))


class FakeShow:
    def __init__(self, position=1):
        self.calls: list[str] = []
        self.View = Obj(
            CurrentShowPosition=position,
            GotoSlide=lambda i: self.calls.append(f"goto {i}"),
            Next=lambda: self.calls.append("next"),
            Previous=lambda: self.calls.append("previous"),
            Exit=lambda: self.calls.append("exit"),
        )


@dataclass
class FakePresentation:
    Name: str
    Path: str = ""
    slides: int = 3
    show: object | None = None
    view_index: int = 1
    saved: int = 0
    saved_as: list = field(default_factory=list)

    def __post_init__(self):
        self.view = FakeView(self.view_index)
        self.Slides = Obj(Count=self.slides)
        self.Windows = Coll([Obj(View=self.view)])
        self.SlideShowSettings = Obj(Run=self._run)
        self.started = None

    @property
    def FullName(self):
        return f"{self.Path}\\{self.Name}" if self.Path else self.Name

    @property
    def SlideShowWindow(self):
        if self.show is None:
            raise RuntimeError("no slide show")
        return self.show

    def _run(self):
        self.started = FakeShow(1)
        self.show = self.started
        return self.started

    def Save(self):
        self.saved += 1

    def SaveAs(self, path, fmt):
        self.saved_as.append((path, fmt))


class FakeApp:
    def __init__(self, *presentations, active=None):
        self.Presentations = Coll(presentations)
        self.ActivePresentation = active or (presentations[0] if presentations else None)


def test_save_unsaved_presentation_asks_for_name():
    pres = FakePresentation("Bai.pptx", Path="")
    result = ppt.save_presentation(FakeApp(pres), "Bai.pptx - PowerPoint")
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "unsaved"
    assert pres.saved == 0


def test_save_saved_presentation_calls_save():
    pres = FakePresentation("Bai.pptx", Path="C:\\docs")
    assert ppt.save_presentation(FakeApp(pres), "Bai.pptx - PowerPoint").status is ResultStatus.OK
    assert pres.saved == 1


def test_save_as_next_to_current_file(tmp_path):
    folder = tmp_path / "Đề tài"
    folder.mkdir()
    pres = FakePresentation("Bai.pptx", Path=str(folder))
    result = ppt.save_as_presentation(FakeApp(pres), "Bai.pptx", "Báo cáo", tmp_path / "default")
    assert result.status is ResultStatus.OK
    path, fmt = pres.saved_as[0]
    assert Path(path) == folder / "Báo cáo.pptx"
    assert fmt == ppt.PP_SAVE_AS_OPEN_XML_PRESENTATION


def test_save_as_unsaved_uses_default_folder_and_creates_it(tmp_path):
    pres = FakePresentation("Bản nháp", Path="")
    default = tmp_path / "Documents" / "VoicePilot"
    ppt.save_as_presentation(FakeApp(pres), "Bản nháp", "Hợp đồng 2026", default)
    assert default.is_dir()
    assert Path(pres.saved_as[0][0]) == default / "Hợp đồng 2026.pptx"


def test_save_as_adds_number_for_existing_name(tmp_path):
    (tmp_path / "Báo cáo.pptx").write_bytes(b"")
    pres = FakePresentation("Bai.pptx", Path="")
    ppt.save_as_presentation(FakeApp(pres), "Bai.pptx", "Báo cáo", tmp_path)
    assert Path(pres.saved_as[0][0]) == tmp_path / "Báo cáo (2).pptx"


@pytest.mark.parametrize("spoken", ["../../Windows/system32/x", "CON", "a/b", ""])
def test_save_as_invalid_name_never_reaches_powerpoint(tmp_path, spoken):
    pres = FakePresentation("Bai.pptx", Path=str(tmp_path))
    result = ppt.save_as_presentation(FakeApp(pres), "Bai.pptx", spoken, tmp_path)
    assert result.status is ResultStatus.INVALID_PARAM
    assert result.error_code == "invalid_filename"
    assert pres.saved_as == []


def test_next_slide_in_edit_view_moves_forward():
    pres = FakePresentation("Bai.pptx", slides=3, view_index=1)
    result = ppt.next_slide(FakeApp(pres), "Bai.pptx")
    assert result.status is ResultStatus.OK
    assert pres.view.calls == [("goto", 2)]


def test_next_slide_on_last_slide_is_boundary():
    pres = FakePresentation("Bai.pptx", slides=3, view_index=3)
    result = ppt.next_slide(FakeApp(pres), "Bai.pptx")
    assert result.status is ResultStatus.BOUNDARY
    assert pres.view.calls == []


def test_previous_slide_on_first_slide_is_boundary():
    pres = FakePresentation("Bai.pptx", slides=3, view_index=1)
    assert ppt.previous_slide(FakeApp(pres), "Bai.pptx").status is ResultStatus.BOUNDARY
    assert pres.view.calls == []


def test_slide_show_navigation_respects_boundaries():
    show = FakeShow(position=3)
    pres = FakePresentation("Bai.pptx", slides=3, show=show)
    assert ppt.next_slide(FakeApp(pres), "Bai.pptx").status is ResultStatus.BOUNDARY
    assert show.calls == []
    show.View.CurrentShowPosition = 1
    assert ppt.previous_slide(FakeApp(pres), "Bai.pptx").status is ResultStatus.BOUNDARY
    assert show.calls == []


def test_slide_show_next_moves_when_inside_range():
    show = FakeShow(position=2)
    pres = FakePresentation("Bai.pptx", slides=3, show=show)
    assert ppt.next_slide(FakeApp(pres), "Bai.pptx").status is ResultStatus.OK
    assert show.calls == ["next"]


def test_start_show_runs_from_first_slide_and_stop_returns_to_edit():
    pres = FakePresentation("Bai.pptx", slides=3)
    assert ppt.start_show(FakeApp(pres), "Bai.pptx").status is ResultStatus.OK
    assert pres.started.calls == ["goto 1"]
    assert ppt.stop_show(FakeApp(pres), "Bai.pptx").status is ResultStatus.OK
    assert pres.show.calls[-1] == "exit"


def test_stop_show_without_running_show_is_harmless():
    pres = FakePresentation("Bai.pptx", show=None)
    result = ppt.stop_show(FakeApp(pres), "Bai.pptx")
    assert result.status is ResultStatus.OK
    assert pres.show is None


def test_title_match_picks_the_named_presentation():
    a = FakePresentation("a.pptx", Path="C:\\x")
    ba = FakePresentation("ba.pptx", Path="C:\\y")
    app = FakeApp(a, ba, active=a)
    assert ppt.resolve_target(app, "BA.PPTX - PowerPoint").presentation is ba


def test_title_match_prefers_longest_name():
    assert ppt.match_by_title("Quarterly.pptx - PowerPoint", ["Quarter", "Quarterly.pptx"]) == 1
    assert ppt.match_by_title("", ["x"]) is None


def test_no_open_presentation_is_not_available_through_controller():
    controller = ppt.PowerPointController(app_source=lambda: FakeApp(), apartment=_no_com)
    try:
        result = controller.next_slide(_ctx(AppKind.POWERPOINT, "Bai.pptx - PowerPoint"))
        assert result.status is ResultStatus.NOT_AVAILABLE
        assert result.error_code == "ppt_no_document"
    finally:
        controller.shutdown()


def _no_com():
    import contextlib

    return contextlib.nullcontext()


def _ctx(kind: AppKind, title: str, hwnd: int = 9) -> WindowContext:
    return WindowContext(
        hwnd=hwnd, pid=4, process_name="POWERPNT.EXE", title=title, app_kind=kind, browser=None
    )


def test_powerpoint_worker_maps_unavailable_and_unexpected_errors():
    def source():
        raise ppt.PowerPointUnavailable("PowerPoint chưa mở.", "ppt_not_running")

    controller = ppt.PowerPointController(app_source=source, apartment=_no_com)
    try:
        assert controller.save(_ctx(AppKind.POWERPOINT, "x")).error_code == "ppt_not_running"
    finally:
        controller.shutdown()

    def broken():
        raise OSError("COM died")

    controller = ppt.PowerPointController(app_source=broken, apartment=_no_com)
    try:
        result = controller.save(_ctx(AppKind.POWERPOINT, "x"))
        assert result.status is ResultStatus.ERROR
        assert result.error_code == "ppt_error"
    finally:
        controller.shutdown()


def test_powerpoint_worker_times_out_instead_of_hanging():
    def slow_source():
        time.sleep(1.0)
        return FakeApp(FakePresentation("a.pptx"))

    controller = ppt.PowerPointController(app_source=slow_source, apartment=_no_com, timeout_s=0.2)
    try:
        result = controller.next_slide(_ctx(AppKind.POWERPOINT, "a.pptx"))
        assert result.error_code == "ppt_timeout"
    finally:
        controller.shutdown()


def test_powerpoint_worker_runs_jobs_on_one_thread_and_restarts_after_shutdown(tmp_path):
    threads = []

    def source():
        threads.append(threading.get_ident())
        return FakeApp(FakePresentation("a.pptx", Path=""))

    controller = ppt.PowerPointController(app_source=source, apartment=_no_com, save_dir=tmp_path)
    try:
        controller.save(_ctx(AppKind.POWERPOINT, "a.pptx"))
        controller.save(_ctx(AppKind.POWERPOINT, "a.pptx"))
        assert len(set(threads)) == 1
        controller.shutdown()
        controller.save(_ctx(AppKind.POWERPOINT, "a.pptx"))  # worker is started again
        assert len(threads) == 3  # thread idents can be reused by the OS, so count jobs instead
    finally:
        controller.shutdown()


# --- WindowsExecutor with injected fakes -----------------------------------------------


class FakePowerPoint:
    def __init__(self):
        self.calls: list[str] = []

    def _rec(self, name):
        self.calls.append(name)
        return ActionResult(ResultStatus.OK, name)

    def save(self, ctx):
        return self._rec("save")

    def save_as(self, ctx, filename):
        return self._rec(f"save_as:{filename}")

    def next_slide(self, ctx):
        return self._rec("next")

    def previous_slide(self, ctx):
        return self._rec("prev")

    def start_show(self, ctx):
        return self._rec("start")

    def stop_show(self, ctx):
        return self._rec("stop")

    def shutdown(self):
        pass


class FakeKeys:
    def __init__(self, fail: Exception | None = None):
        self.hotkeys: list[tuple] = []
        self.typed: list[str] = []
        self.fail = fail

    def send_hotkey(self, *keys):
        if self.fail is not None:
            raise self.fail
        self.hotkeys.append(keys)

    def type_unicode(self, text):
        self.typed.append(text)


class FakeContext:
    def __init__(self, still: bool = True):
        self.still = still

    def capture(self):
        raise AssertionError("executor must not capture")

    def is_still_target(self, ctx):
        return self.still


def ctx_of(kind: AppKind, *, hwnd: int = 42, browser_name: str | None = None) -> WindowContext:
    return WindowContext(
        hwnd=hwnd, pid=7, process_name="x.exe", title="t", app_kind=kind, browser=browser_name
    )


def make_executor(*, still=True, keys=None, ppt_backend=None):
    keys = keys or FakeKeys()
    ppt_backend = ppt_backend or FakePowerPoint()
    ex = executor_mod.WindowsExecutor(AppConfig(), FakeContext(still), powerpoint=ppt_backend, keys=keys)
    return ex, keys, ppt_backend


def cmd(intent: Intent, **params) -> Command:
    return Command(intent, "raw", params=params)


@pytest.mark.parametrize(
    "intent,params",
    [
        (Intent.PPT_SAVE, {}),
        (Intent.PPT_SAVE_AS, {"filename": "Báo cáo"}),
        (Intent.PPT_NEXT_SLIDE, {}),
        (Intent.PPT_PREV_SLIDE, {}),
        (Intent.PPT_START_SHOW, {}),
        (Intent.PPT_STOP_SHOW, {}),
    ],
)
@pytest.mark.parametrize("kind", [AppKind.BROWSER, AppKind.OTHER, AppKind.NONE])
def test_powerpoint_intents_never_touch_powerpoint_outside_powerpoint(intent, params, kind):
    ex, _, backend = make_executor()
    result = ex.execute(
        cmd(intent, **params), ctx_of(kind, browser_name="edge" if kind is AppKind.BROWSER else None)
    )
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert backend.calls == []


def test_powerpoint_intent_with_changed_window_is_retry_and_not_executed():
    ex, _, backend = make_executor(still=False)
    result = ex.execute(cmd(Intent.PPT_NEXT_SLIDE), ctx_of(AppKind.POWERPOINT))
    assert result.status is ResultStatus.NEEDS_RETRY
    assert backend.calls == []


def test_powerpoint_intent_in_powerpoint_is_dispatched():
    ex, _, backend = make_executor()
    assert (
        ex.execute(cmd(Intent.PPT_SAVE_AS, filename="Báo cáo"), ctx_of(AppKind.POWERPOINT)).status
        is ResultStatus.OK
    )
    assert backend.calls == ["save_as:Báo cáo"]


@pytest.mark.parametrize(
    "intent,kind,hotkey",
    [
        (Intent.BROWSER_NEXT_TAB, AppKind.BROWSER, ("ctrl", "tab")),
        (Intent.BROWSER_PREV_TAB, AppKind.BROWSER, ("ctrl", "shift", "tab")),
        (Intent.BROWSER_NEW_TAB, AppKind.BROWSER, ("ctrl", "t")),
        (Intent.BROWSER_CLOSE_TAB, AppKind.BROWSER, ("ctrl", "w")),
    ],
)
def test_tab_commands_send_keys_only_to_browser(intent, kind, hotkey):
    ex, keys, _ = make_executor()
    assert ex.execute(cmd(intent), ctx_of(kind, browser_name="chrome")).status is ResultStatus.OK
    assert keys.hotkeys == [hotkey]


@pytest.mark.parametrize(
    "intent", [Intent.BROWSER_NEXT_TAB, Intent.BROWSER_CLOSE_TAB, Intent.BROWSER_NEW_TAB]
)
@pytest.mark.parametrize("kind", [AppKind.POWERPOINT, AppKind.OTHER, AppKind.NONE])
def test_tab_commands_send_no_keys_outside_browser(intent, kind):
    ex, keys, _ = make_executor()
    result = ex.execute(cmd(intent), ctx_of(kind))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert keys.hotkeys == []


def test_tab_command_with_changed_window_sends_no_keys():
    ex, keys, _ = make_executor(still=False)
    assert (
        ex.execute(cmd(Intent.BROWSER_CLOSE_TAB), ctx_of(AppKind.BROWSER, browser_name="edge")).status
        is ResultStatus.NEEDS_RETRY
    )
    assert keys.hotkeys == []


def test_open_site_runs_without_any_window(monkeypatch):
    opened = []
    monkeypatch.setattr(browser, "launch_url", lambda url, ctx: opened.append((url, ctx.app_kind)))
    ex, _, _ = make_executor()
    result = ex.execute(
        cmd(Intent.BROWSER_OPEN_SITE, url="https://www.youtube.com", site="youtube"),
        ctx_of(AppKind.NONE, hwnd=0),
    )
    assert result.status is ResultStatus.OK
    assert opened == [("https://www.youtube.com", AppKind.NONE)]


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "https://", "file:///C:/", "https://exa mple.com"],
)
def test_open_site_revalidates_url_before_launch(monkeypatch, url):
    opened = []
    monkeypatch.setattr(browser, "launch_url", lambda u, ctx: opened.append(u))
    ex, _, _ = make_executor()
    result = ex.execute(
        cmd(Intent.BROWSER_OPEN_SITE, url=url, site="x"), ctx_of(AppKind.BROWSER, browser_name="edge")
    )
    assert result.status is ResultStatus.INVALID_PARAM
    assert opened == []


def test_open_site_launch_failure_is_error_result(monkeypatch):
    def fail(url, ctx):
        raise ExecutionError("no browser")

    monkeypatch.setattr(browser, "launch_url", fail)
    ex, _, _ = make_executor()
    result = ex.execute(
        cmd(Intent.BROWSER_OPEN_SITE, url="https://example.com", site="x"), ctx_of(AppKind.OTHER)
    )
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "launch_failed"


def test_search_opens_encoded_google_url(monkeypatch):
    opened = []
    monkeypatch.setattr(browser, "launch_url", lambda url, ctx: opened.append(url))
    ex, _, _ = make_executor()
    query = "C++ & Java #1"
    result = ex.execute(cmd(Intent.BROWSER_SEARCH, query=query), ctx_of(AppKind.OTHER))
    assert result.status is ResultStatus.OK
    assert parse_qs(urlsplit(opened[0]).query)["q"] == [query]


def test_search_changed_window_does_not_open_anything(monkeypatch):
    opened = []
    monkeypatch.setattr(browser, "launch_url", lambda url, ctx: opened.append(url))
    ex, _, _ = make_executor(still=False)
    result = ex.execute(
        cmd(Intent.BROWSER_SEARCH, query="giá vàng"), ctx_of(AppKind.BROWSER, browser_name="edge")
    )
    assert result.status is ResultStatus.NEEDS_RETRY
    assert opened == []


def test_open_app_uses_alias_target_not_spoken_text(monkeypatch):
    started = []
    monkeypatch.setattr(desktop, "open_app", lambda target: started.append(target))
    ex, _, _ = make_executor()
    result = ex.execute(
        cmd(Intent.WIN_OPEN_APP, app="POWERPNT.EXE", app_name="powerpoint"), ctx_of(AppKind.OTHER)
    )
    assert result.status is ResultStatus.OK
    assert started == ["POWERPNT.EXE"]


def test_open_app_failure_is_not_available(monkeypatch):
    def fail(target):
        raise ExecutionError("nope")

    monkeypatch.setattr(desktop, "open_app", fail)
    ex, _, _ = make_executor()
    result = ex.execute(cmd(Intent.WIN_OPEN_APP, app="WINWORD.EXE", app_name="word"), ctx_of(AppKind.OTHER))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.error_code == "app_launch_failed"


def test_screenshot_runs_from_any_window_and_reports_path(monkeypatch, tmp_path):
    target = tmp_path / "VoicePilot_20260101_000000.png"
    monkeypatch.setattr(desktop, "screenshot", lambda ctx, hide_overlay=None: target)
    ex, _, _ = make_executor()
    result = ex.execute(cmd(Intent.WIN_SCREENSHOT), ctx_of(AppKind.NONE, hwnd=0))
    assert result.status is ResultStatus.OK
    assert result.data["path"] == str(target)


def test_dictation_types_text_only_in_a_still_valid_window():
    ex, keys, _ = make_executor()
    result = ex.execute(cmd(Intent.DICTATE_TEXT, text="lưu"), ctx_of(AppKind.OTHER))
    assert result.status is ResultStatus.OK
    assert keys.typed == ["lưu"]


def test_dictation_with_changed_window_types_nothing():
    ex, keys, _ = make_executor(still=False)
    assert (
        ex.execute(cmd(Intent.DICTATE_TEXT, text="lưu"), ctx_of(AppKind.OTHER)).status
        is ResultStatus.NEEDS_RETRY
    )
    assert keys.typed == []


def test_send_input_refusal_is_reported_not_raised():
    class Typing(FakeKeys):
        def type_unicode(self, text):
            raise ExecutionError("UIPI")

    ex, _, _ = make_executor(keys=Typing())
    result = ex.execute(cmd(Intent.DICTATE_TEXT, text="x"), ctx_of(AppKind.OTHER))
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "send_input_failed"


def test_not_executable_command_has_no_side_effects():
    ex, keys, backend = make_executor()
    result = ex.execute(
        Command(Intent.BROWSER_SEARCH, "tìm", missing=("query",), example="Tìm giá vàng"),
        ctx_of(AppKind.BROWSER, browser_name="edge"),
    )
    assert result.status is ResultStatus.INVALID_PARAM
    assert "Tìm giá vàng" in result.message_vi
    assert keys.hotkeys == [] and backend.calls == []


def test_unexpected_handler_exception_is_contained(monkeypatch):
    def boom(url, ctx):
        raise RuntimeError("driver crashed")

    monkeypatch.setattr(browser, "launch_url", boom)
    ex, _, _ = make_executor()
    result = ex.execute(cmd(Intent.BROWSER_SEARCH, query="x"), ctx_of(AppKind.OTHER))
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "exception"
