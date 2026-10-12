"""Acceptance for blank Office documents and browser windows, including Home regression."""

import contextlib
import threading
from unittest.mock import Mock

import pytest

from voicepilot.config import AppConfig
from voicepilot.contracts import AppKind, Intent, Mode, ResultStatus, WindowContext
from voicepilot.intent.parser import RuleParser
from voicepilot.intent.validation import is_valid_candidate
from voicepilot.windows.executor import WindowsExecutor

CASES = [
    ("WORD_NEW_DOCUMENT", "word", "WINWORD.EXE", "tạo tài liệu Word mới"),
    ("EXCEL_NEW_WORKBOOK", "excel", "EXCEL.EXE", "tạo bảng tính mới"),
    ("PPT_NEW_PRESENTATION", "powerpoint", "POWERPNT.EXE", "tạo bài trình chiếu mới"),
    ("BROWSER_NEW_WINDOW", "browser", "msedge.exe", "mở cửa sổ mới"),
    ("BROWSER_PRIVATE_WINDOW", "browser", "chrome.exe", "mở cửa sổ ẩn danh"),
]


def context(kind, exe):
    return WindowContext(1, 2, exe, "Fixture", AppKind(kind), "edge" if kind == "browser" else None)


@pytest.mark.parametrize("name,kind,exe,phrase", CASES)
def test_new_phrases_and_dictation(name, kind, exe, phrase):
    ctx = context(kind, exe)
    command = RuleParser({}, {}).parse(phrase, ctx, Mode.COMMAND)
    assert command.intent.value == name
    assert command.is_executable
    assert is_valid_candidate(command, ctx, [])
    assert not is_valid_candidate(command, context("other", "notepad.exe"), [])
    assert RuleParser({}, {}).parse(phrase, ctx, Mode.DICTATION).params == {"text": phrase}


@pytest.mark.parametrize("name,kind,exe,_example", CASES[:3])
@pytest.mark.parametrize("phrase", ["tạo file mới", "tạo tệp mới", "file mới", "tạo file trắng"])
def test_generic_new_file_uses_current_office(name, kind, exe, _example, phrase):
    assert RuleParser({}, {}).parse(phrase, context(kind, exe), Mode.COMMAND).intent.value == name


def test_generic_new_file_does_not_guess_an_app():
    assert (
        RuleParser({}, {}).parse("tạo file mới", context("other", "notepad.exe"), Mode.COMMAND).intent
        is Intent.UNKNOWN
    )


def test_create_new_tab_phrase():
    assert (RuleParser({}, {}).parse("tạo tab mới", context("browser", "msedge.exe"), Mode.COMMAND).intent
            is Intent.BROWSER_NEW_TAB)


@pytest.mark.parametrize("name,kind,exe,phrase", CASES)
def test_custom_patterns_overrides_and_hotwords(name, kind, exe, phrase):
    from voicepilot.builtin_commands import command_table
    from voicepilot.custom_commands import validate_commands

    item = dict(id="new", name="New", patterns=["lệnh của tôi"], intent=name, params={})
    validated = validate_commands([item])
    ctx = context(kind, exe)
    parser = RuleParser({}, {}, custom_commands=validated, builtin_patterns={name: ["lệnh gốc sửa"]})
    assert parser.parse("lệnh của tôi", ctx, Mode.COMMAND).intent.value == name
    assert parser.parse("lệnh gốc sửa", ctx, Mode.COMMAND).intent.value == name
    table = command_table({name: ["lệnh gốc sửa"]})
    assert next(row for row in table["intent"] if row["name"] == name)["patterns"] == ["lệnh gốc sửa"]


@pytest.mark.parametrize("name,kind,exe,phrase", CASES)
@pytest.mark.parametrize("blocked", ["wrong_app", "changed", "cancel"])
def test_executor_blocks_incompatible_or_abandoned_turns(name, kind, exe, phrase, blocked):
    ctx = context(kind, exe)
    command = RuleParser({}, {}).parse(phrase, ctx, Mode.COMMAND)
    provider = Mock(is_still_target=Mock(return_value=blocked != "changed"))
    keys, office = Mock(), Mock()
    executor = WindowsExecutor(AppConfig(), provider, keys=keys, office=office)
    cancel = threading.Event()
    if blocked == "cancel":
        cancel.set()
    if blocked == "wrong_app":
        ctx = context("other", "notepad.exe")
    try:
        assert executor.execute(command, ctx, cancel=cancel).status is not ResultStatus.OK
        assert not keys.mock_calls and not office.mock_calls
    finally:
        executor.shutdown()


@pytest.mark.parametrize("name,kind,exe,phrase", CASES[3:])
def test_browser_window_keys(name, kind, exe, phrase):
    ctx = context(kind, exe)
    keys = Mock()
    executor = WindowsExecutor(AppConfig(), Mock(is_still_target=Mock(return_value=True)), keys=keys)
    try:
        result = executor.execute(RuleParser({}, {}).parse(phrase, ctx, Mode.COMMAND), ctx)
        assert result.status is ResultStatus.OK
        keys.send_hotkey.assert_called_once_with(
            *(("ctrl", "n") if name == "BROWSER_NEW_WINDOW" else ("ctrl", "shift", "n"))
        )
    finally:
        executor.shutdown()


class FakeClock:
    """Monotonic fake: each read advances by step, and sleep advances the clock."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


ROOT = 1000
HOME = 2000


class _FakeDispatch:
    """Stands in for the pythoncom wrapper: Invoke on the object (child 0) or on a child id."""

    def __init__(self, com: "FakeCOM", pointer: int) -> None:
        self._oleobj_ = self
        self._com = com
        self._pointer = pointer
        self.accChildCount = len(com.objects[pointer]["kids"])

    def Invoke(self, dispid, lcid, flags, want, child_id=0):  # noqa: N802 - mirrors COM naming
        self._com.check_live(self._pointer)
        obj = self._com.objects[self._pointer]
        if dispid == -5018:
            self._com.events.append(("invoke", self._pointer, child_id))
            return None
        table = obj["ids"][child_id] if child_id else obj
        values = {-5003: table.get("name", ""), -5006: table.get("role", 0), -5007: table.get("state", 0)}
        return values[dispid]


class FakeCOM:
    """Fake MSAA tree. Kids are ("ptr", pointer) IAccessible objects or ("id", child_id) children."""

    def __init__(self, objects: dict) -> None:
        self.objects = objects
        self.events: list[tuple] = []
        self.released: set[int] = set()

    def check_live(self, pointer: int) -> None:
        if pointer in self.released:
            self.events.append(("use_after_release", pointer))

    def open_root(self, hwnd: int) -> int:
        self.events.append(("open", hwnd))
        return ROOT

    def dispatch_for(self, pointer: int) -> _FakeDispatch:
        self.check_live(pointer)
        return _FakeDispatch(self, pointer)

    def children(self, pointer: int, count: int, session) -> list[tuple[str, int]]:
        self.check_live(pointer)
        out = []
        for kind, value in self.objects[pointer]["kids"]:
            if kind == "ptr":
                session.own(value)
                out.append(("accessible", value))
            else:
                out.append(("child", value))
        return out

    def release(self, pointer: int) -> None:
        if pointer in self.released:
            self.events.append(("double_release", pointer))
        self.released.add(pointer)
        self.events.append(("release", pointer))

    def install(self, monkeypatch) -> None:
        from voicepilot.windows import office

        monkeypatch.setattr(office, "_open_root", self.open_root)
        monkeypatch.setattr(office, "_dispatch_for", self.dispatch_for)
        monkeypatch.setattr(office, "_accessible_children", self.children)
        monkeypatch.setattr(office, "_release", self.release)


def home_tree(*items, role: int = 0x10) -> dict:
    """Word-like window: root -> Home pane (ROOT/HOME) -> child-id objects (ids 7, 8, ...)."""
    ids = {number: dict(name=name, role=tile_role, state=state)
           for number, (name, tile_role, state) in enumerate(items, start=7)}
    return {
        ROOT: dict(name="Word", role=0x01, state=0, kids=[("ptr", HOME)]),
        HOME: dict(name="Home", role=role, state=0, kids=[("id", number) for number in ids], ids=ids),
    }


def office_controller(provider=None, *, clock=None, foreground=None, blank_source=None, **kwargs):
    from voicepilot.windows.office import OfficeController

    clock = clock or FakeClock()
    return OfficeController(
        context_provider=provider or Mock(is_still_target=Mock(return_value=True)),
        apartment=contextlib.nullcontext,
        clock=clock,
        sleep=clock.sleep,
        foreground=foreground or (lambda: (0, 0)),
        blank_source=blank_source,
        **kwargs,
    )


@pytest.mark.parametrize("name,kind,exe,_phrase", CASES[:3])
def test_office_blank_tile_skips_ctrl_n_and_reports_intent_message(name, kind, exe, _phrase):
    from voicepilot.windows.office import OFFICE_MESSAGES

    intent = Intent(name)
    keys = Mock()
    blank = Mock()
    def blank_source(ctx, guard):
        blank()
        return True

    controller = office_controller(blank_source=blank_source)
    try:
        result = controller.new_blank(context(kind, exe), keys, intent, cancel=None)
    finally:
        controller.shutdown()
    assert result.status is ResultStatus.OK
    assert result.message_vi == OFFICE_MESSAGES[intent]
    blank.assert_called_once_with()
    keys.send_hotkey.assert_not_called()


def test_office_messages_name_each_document_type():
    from voicepilot.windows.office import OFFICE_MESSAGES

    assert OFFICE_MESSAGES[Intent.EXCEL_NEW_WORKBOOK] == "Đã tạo bảng tính Excel trắng."
    assert OFFICE_MESSAGES[Intent.WORD_NEW_DOCUMENT] == "Đã tạo tài liệu Word trắng."
    assert OFFICE_MESSAGES[Intent.PPT_NEW_PRESENTATION] == "Đã tạo bài trình chiếu trắng."


@pytest.mark.parametrize("name,kind,exe,_phrase", CASES[:3])
@pytest.mark.parametrize(
    "foreground,verified",
    [
        (lambda: (2, 2), True),  # a new window of the same process came to the front
        (lambda: (1, 2), False),  # the original window is still in front
        (lambda: (2, 3), False),  # a window of another process
        (lambda: (0, 0), False),  # no foreground window
    ],
)
def test_ctrl_n_is_reported_only_when_a_new_window_is_verified(
    name, kind, exe, _phrase, foreground, verified
):
    from voicepilot.windows.office import OFFICE_MESSAGES

    intent = Intent(name)
    keys = Mock()
    clock = FakeClock()
    controller = office_controller(
        clock=clock, foreground=foreground, blank_source=lambda ctx, guard: False
    )
    try:
        result = controller.new_blank(context(kind, exe), keys, intent)
    finally:
        controller.shutdown()
    keys.send_hotkey.assert_called_once_with("ctrl", "n")
    if verified:
        assert result.status is ResultStatus.OK
        assert result.message_vi == OFFICE_MESSAGES[intent]
    else:
        assert result.status is ResultStatus.ERROR
        assert result.error_code == "office_new_unverified"
        assert result.message_vi == "Office không tạo được tài liệu mới."
        assert clock.now >= 2.4


def test_ctrl_n_waits_for_a_late_new_window(monkeypatch):
    keys = Mock()
    answers = iter([(1, 2), (1, 2), (2, 2)])
    clock = FakeClock()
    controller = office_controller(
        clock=clock, foreground=lambda: next(answers), blank_source=lambda ctx, guard: False
    )
    try:
        result = controller.new_blank(context("word", "WINWORD.EXE"), keys, Intent.WORD_NEW_DOCUMENT)
    finally:
        controller.shutdown()
    assert result.status is ResultStatus.OK
    assert clock.now == pytest.approx(0.2)


def test_cancel_during_tile_search_prevents_ctrl_n():
    keys = Mock()
    cancel = threading.Event()

    def blank_source(ctx, guard):
        cancel.set()
        return False

    controller = office_controller(blank_source=blank_source)
    try:
        result = controller.new_blank(
            context("excel", "EXCEL.EXE"), keys, Intent.EXCEL_NEW_WORKBOOK, cancel=cancel
        )
    finally:
        controller.shutdown()
    assert result.status is not ResultStatus.OK
    keys.send_hotkey.assert_not_called()


@pytest.mark.parametrize("abandon", ["focus", "cancel", "timeout"])
def test_home_probe_cannot_invoke_blank_after_abandonment(abandon):
    ctx = context("excel", "EXCEL.EXE")
    cancel = threading.Event()
    provider = Mock(is_still_target=Mock(return_value=True))
    entered, release = threading.Event(), threading.Event()
    blank, keys = Mock(), Mock()

    def probe(ctx, guard):
        entered.set()
        if abandon == "timeout":
            release.wait(1)
        elif abandon == "focus":
            provider.is_still_target.return_value = False
        else:
            cancel.set()
        guard()  # the session re-checks before the tile is pressed
        blank()
        return True

    controller = office_controller(provider, blank_source=probe, timeout_s=0.03)
    try:
        result = controller.new_blank(ctx, keys, Intent.EXCEL_NEW_WORKBOOK, cancel=cancel)
        assert result.status is not ResultStatus.OK
        release.set()
        controller.shutdown()
        assert entered.is_set()
        blank.assert_not_called()
        keys.send_hotkey.assert_not_called()
    finally:
        release.set()
        controller.shutdown()


def test_home_probe_error_does_not_fall_back_to_unverified_shortcut():
    keys = Mock()
    probe = Mock(side_effect=OSError("accessibility unavailable"))
    controller = office_controller(blank_source=probe)
    try:
        result = controller.new_blank(context("excel", "EXCEL.EXE"), keys, Intent.EXCEL_NEW_WORKBOOK)
        assert result.status is not ResultStatus.OK
        keys.send_hotkey.assert_not_called()
    finally:
        controller.shutdown()


def test_tile_is_invoked_inside_session_after_guard_and_before_release(monkeypatch):
    com = FakeCOM(home_tree(("Blank document", 0x22, 0), ("Blank document", 0x22, 0x8000)))
    com.install(monkeypatch)
    answers = iter([True, True, True])
    provider = Mock()
    provider.is_still_target.side_effect = lambda _ctx: (
        com.events.append(("guard",)) or next(answers, True)
    )
    keys = Mock()
    controller = office_controller(provider)
    try:
        result = controller.new_blank(context("word", "WINWORD.EXE"), keys, Intent.WORD_NEW_DOCUMENT)
    finally:
        controller.shutdown()
    assert result.status is ResultStatus.OK
    invoke_at = com.events.index(("invoke", HOME, 7))
    guards_before = [index for index, event in enumerate(com.events[:invoke_at]) if event == ("guard",)]
    assert guards_before and guards_before[-1] == invoke_at - 1
    assert not any(event[0] == "release" for event in com.events[:invoke_at])
    assert com.events[-2:] == [("release", HOME), ("release", ROOT)]
    assert not any(event[0] in ("use_after_release", "double_release") for event in com.events)
    assert ("invoke", HOME, 8) not in com.events
    keys.send_hotkey.assert_not_called()


def test_guard_failure_before_invoke_releases_pointers_without_pressing(monkeypatch):
    com = FakeCOM(home_tree(("Blank document", 0x22, 0)))
    com.install(monkeypatch)
    answers = iter([True, True, False])
    provider = Mock()
    provider.is_still_target.side_effect = lambda _ctx: next(answers, False)
    keys = Mock()
    controller = office_controller(provider)
    try:
        result = controller.new_blank(context("word", "WINWORD.EXE"), keys, Intent.WORD_NEW_DOCUMENT)
    finally:
        controller.shutdown()
    assert result.status is ResultStatus.NEEDS_RETRY
    assert not any(event[0] == "invoke" for event in com.events)
    keys.send_hotkey.assert_not_called()
    assert com.events[-2:] == [("release", HOME), ("release", ROOT)]


@pytest.mark.parametrize(
    "items",
    [
        [("Blank document", 0x1E, 0)],  # a recent file (link role) with the template name
        [("Blank document", 0x22, 0), ("Blank document", 0x2B, 0)],  # two candidates are ambiguous
        [("Blank document", 0x22, 0x8000)],  # only a hidden tile
    ],
)
def test_only_one_visible_list_item_button_or_menu_item_is_pressed(monkeypatch, items):
    com = FakeCOM(home_tree(*items))
    com.install(monkeypatch)
    keys = Mock()
    controller = office_controller(foreground=lambda: (2, 2))
    try:
        result = controller.new_blank(context("word", "WINWORD.EXE"), keys, Intent.WORD_NEW_DOCUMENT)
    finally:
        controller.shutdown()
    assert not any(event[0] == "invoke" for event in com.events)
    keys.send_hotkey.assert_called_once_with("ctrl", "n")
    assert result.status is ResultStatus.OK


@pytest.mark.parametrize("role", [0x0C, 0x22, 0x2B])
def test_menu_item_list_item_and_button_tiles_are_accepted(monkeypatch, role):
    com = FakeCOM(home_tree(("Blank document", role, 0)))
    com.install(monkeypatch)
    keys = Mock()
    controller = office_controller()
    try:
        result = controller.new_blank(context("word", "WINWORD.EXE"), keys, Intent.WORD_NEW_DOCUMENT)
    finally:
        controller.shutdown()
    assert result.status is ResultStatus.OK
    assert ("invoke", HOME, 7) in com.events
    keys.send_hotkey.assert_not_called()


def _walk(com, **kwargs):
    from voicepilot.windows import office
    from voicepilot.windows.office import BLANK_NAMES

    with office._Accessible.root(1) as root:
        return office._find_blank_tiles(root, BLANK_NAMES[AppKind.WORD], **kwargs)


def test_walk_stops_quietly_at_the_node_budget(monkeypatch):
    items = [("Other", 0x22, 0)] * 9 + [("Blank document", 0x22, 0)]
    com = FakeCOM(home_tree(*items))
    com.install(monkeypatch)
    assert len(_walk(com, budget=50)) == 1
    assert _walk(com, budget=5) == []  # not found, never an exception


def test_walk_depth_limit_is_not_found(monkeypatch):
    objects = {ROOT: dict(name="Word", role=0x01, state=0, kids=[("ptr", 3001)])}
    for depth in range(1, 25):
        kids = [("ptr", 3000 + depth + 1)] if depth < 24 else [("id", 7)]
        ids = {7: dict(name="Blank document", role=0x22, state=0)} if depth == 24 else {}
        objects[3000 + depth] = dict(name="Pane", role=0x10, state=0, kids=kids, ids=ids)
    com = FakeCOM(objects)
    com.install(monkeypatch)
    assert _walk(com) == []
    controller = office_controller(foreground=lambda: (2, 2))
    keys = Mock()
    try:
        result = controller.new_blank(context("word", "WINWORD.EXE"), keys, Intent.WORD_NEW_DOCUMENT)
    finally:
        controller.shutdown()
    assert result.status is ResultStatus.OK
    keys.send_hotkey.assert_called_once_with("ctrl", "n")


def test_walk_deadline_is_not_found(monkeypatch):
    items = [("Other", 0x22, 0)] * 9 + [("Blank document", 0x22, 0)]
    com = FakeCOM(home_tree(*items))
    com.install(monkeypatch)
    assert _walk(com, clock=FakeClock(step=0.5), seconds=2.0) == []
    assert len(_walk(com, clock=FakeClock(step=0.0), seconds=2.0)) == 1


def test_huge_window_falls_back_to_ctrl_n_without_error(monkeypatch):
    com = FakeCOM(home_tree(*[("Other", 0x22, 0)] * 2000))
    com.install(monkeypatch)
    keys = Mock()
    controller = office_controller(foreground=lambda: (2, 2))
    try:
        result = controller.new_blank(context("word", "WINWORD.EXE"), keys, Intent.WORD_NEW_DOCUMENT)
    finally:
        controller.shutdown()
    assert result.status is ResultStatus.OK
    keys.send_hotkey.assert_called_once_with("ctrl", "n")


def test_office_is_a_generic_com_worker_without_powerpoint_actions():
    from voicepilot.windows.com_worker import ComWorker
    from voicepilot.windows.office import OfficeController
    from voicepilot.windows.powerpoint import PowerPointController

    assert issubclass(OfficeController, ComWorker)
    assert not issubclass(OfficeController, PowerPointController)
    for name in ("save", "save_as", "next_slide", "previous_slide", "start_show", "stop_show"):
        assert not hasattr(OfficeController, name)


def test_office_loaders_are_cached_once_per_process(monkeypatch):
    import ctypes

    from voicepilot.windows import office

    loads = []
    monkeypatch.setattr(ctypes, "OleDLL", lambda name: loads.append(name) or Mock(), raising=False)
    office._oleacc.cache_clear()
    office._variant_clear.cache_clear()
    try:
        assert office._oleacc() is office._oleacc()
        assert office._variant_clear() is office._variant_clear()
        assert loads == ["oleacc", "oleaut32"]
    finally:
        office._oleacc.cache_clear()
        office._variant_clear.cache_clear()


@pytest.mark.parametrize("kind,label", [(AppKind.WORD, "Word"), (AppKind.EXCEL, "Excel"),
                                        (AppKind.POWERPOINT, "PowerPoint")])
def test_target_label_names_office_applications(kind, label):
    from voicepilot.controller import target_label

    assert target_label(WindowContext(1, 2, "app.exe", "Fixture", kind, None)) == label


@pytest.mark.parametrize("name,kind,exe,phrase", CASES)
@pytest.mark.parametrize("backend", ["google", "offline", "hybrid", "fallback"])
def test_new_commands_in_full_voice_pipeline(qapp, name, kind, exe, phrase, backend):
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "new_document_harness", Path(__file__).resolve().parents[2] / "tools/verify_features.py"
    )
    harness = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = harness
    spec.loader.exec_module(harness)
    harness.pipeline_case(Intent[name], phrase, AppKind(kind), {}, backend)


def test_old_other_custom_context_still_includes_word_and_excel():
    item = dict(
        id="existing",
        name="Existing",
        patterns=["ký tên"],
        intent="DICTATE_TEXT",
        context="other",
        params={"text": "Nam"},
    )
    parser = RuleParser({}, {}, custom_commands=[item])
    for kind, exe in [("word", "winword.exe"), ("excel", "excel.exe")]:
        assert parser.parse("ký tên", context(kind, exe), Mode.COMMAND).params == {"text": "Nam"}


def test_msaa_children_are_owned_by_session_until_close(monkeypatch):
    """Regression: a child IAccessible stays valid until the walk session closes, not after the query."""
    import ctypes

    from voicepilot.windows import office

    vtable = (ctypes.c_void_p * 3)(11, 12, 13)
    vtable_pointer = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    dispatch_pointer = ctypes.addressof(vtable_pointer)

    def read_children(pointer, start, count, values, obtained):
        assert pointer == 101
        values[0].vt, values[0].value.pointer = 9, dispatch_pointer
        ctypes.cast(obtained, ctypes.POINTER(ctypes.c_int32)).contents.value = 1

    def query(pointer, iid, output):
        assert pointer == dispatch_pointer and iid == office._IID_ACCESSIBLE
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = 202
        return 0

    clear = Mock()
    lib = Mock(AccessibleChildren=Mock(side_effect=read_children))
    monkeypatch.setattr(office, "_oleacc", lambda: lib)
    monkeypatch.setattr(office, "_variant_clear", lambda: clear)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *args: lambda address: query, raising=False)
    release = Mock()
    monkeypatch.setattr(office, "_release", release)
    session = office._Session()
    assert office._accessible_children(101, 1, session) == [("accessible", 202)]
    clear.assert_called_once()
    release.assert_not_called()
    session.close()
    release.assert_called_once_with(202)


def test_office_rejects_window_kind_mismatch_before_any_effect():
    parsed = context("excel", "EXCEL.EXE")
    command = RuleParser({}, {}).parse("tạo bảng tính mới", parsed, Mode.COMMAND)
    ctx = context("other", "notepad.exe")
    keys, office = Mock(), Mock()
    provider = Mock(is_still_target=Mock(return_value=True))
    executor = WindowsExecutor(AppConfig(), provider, keys=keys, office=office)
    try:
        assert executor.execute(command, ctx).status is ResultStatus.NOT_AVAILABLE
        keys.send_hotkey.assert_not_called()
        assert office.mock_calls == []
    finally:
        executor.shutdown()


@pytest.mark.parametrize("exe,kind", [("WINWORD.EXE", AppKind.WORD), ("EXCEL.EXE", AppKind.EXCEL)])
def test_office_context_classifies_native_full_executable_paths(exe, kind):
    from voicepilot.windows.context import classify_process

    assert classify_process("C:\\Program Files\\Microsoft Office\\root\\Office16\\" + exe) == (kind, None)


def test_broken_accessibility_node_falls_back_instead_of_failing(monkeypatch):
    # Review finding: one child without IAccessible (or a stale node) raised through the worker
    # as office_error, so the Ctrl+N fallback never ran.
    import contextlib

    from voicepilot.contracts import AppKind, WindowContext
    from voicepilot.windows import office

    @contextlib.contextmanager
    def root(_hwnd):
        yield object()

    def broken(*_args, **_kwargs):
        raise OSError("Office child does not expose IAccessible")

    monkeypatch.setattr(office._Accessible, "root", staticmethod(root))
    monkeypatch.setattr(office, "_find_blank_tiles", broken)
    guard_calls = []
    ctx = WindowContext(1, 2, "WINWORD.EXE", "Word", AppKind.WORD, None)
    assert office.blank_from_accessibility(ctx, lambda: guard_calls.append(1)) is False
    assert guard_calls == []


def test_tile_walk_frees_nodes_without_the_cyclic_gc():
    # Review finding: the recursive closure kept every visited node (and its COM wrapper) alive
    # until a later cyclic GC, possibly after the worker left its COM apartment.
    import gc
    import weakref

    from voicepilot.windows.office import _find_blank_tiles

    class Node:
        def __init__(self, name, children=()):
            self.name, self.state, self.role, self._children = name, 0, 0x2B, list(children)

        def children(self):
            return iter(self._children)

    leaf = Node("Blank document")
    refs = [weakref.ref(leaf)]
    root = Node("root", [leaf])
    del leaf
    gc.disable()
    try:
        found = _find_blank_tiles(root, {"blank document"})
        assert len(found) == 1
        found.clear()
        root._children.clear()
        assert refs[0]() is None
    finally:
        gc.enable()
