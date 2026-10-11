"""Acceptance for blank Office documents and browser windows, including Home regression."""

import contextlib
import threading
from dataclasses import replace
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


@pytest.mark.parametrize("name,kind,exe,phrase", CASES[:3])
@pytest.mark.parametrize("home", [True, False])
def test_office_home_selects_blank_and_editor_uses_ctrl_n(name, kind, exe, phrase, home):
    from voicepilot.windows.office import OfficeController

    ctx = context(kind, exe)
    keys = Mock()
    blank = Mock() if home else None
    probe = Mock(return_value=blank)
    provider = Mock(is_still_target=Mock(return_value=True))
    controller = OfficeController(
        context_provider=provider, blank_source=probe, apartment=contextlib.nullcontext
    )
    try:
        result = controller.new_blank(ctx, keys, cancel=None)
        assert result.status is ResultStatus.OK
        if home:
            blank.assert_called_once_with()
            keys.send_hotkey.assert_not_called()
        else:
            keys.send_hotkey.assert_called_once_with("ctrl", "n")
    finally:
        controller.shutdown()


@pytest.mark.parametrize("abandon", ["focus", "cancel", "timeout"])
def test_home_probe_cannot_invoke_blank_after_abandonment(abandon):
    from voicepilot.windows.office import OfficeController

    ctx = context("excel", "EXCEL.EXE")
    cancel = threading.Event()
    provider = Mock(is_still_target=Mock(return_value=True))
    entered, release = threading.Event(), threading.Event()
    blank, keys = Mock(), Mock()

    def probe(ctx):
        entered.set()
        if abandon == "timeout":
            release.wait(1)
        elif abandon == "focus":
            provider.is_still_target.return_value = False
        else:
            cancel.set()
        return blank

    controller = OfficeController(
        context_provider=provider, blank_source=probe, apartment=contextlib.nullcontext, timeout_s=0.03
    )
    try:
        result = controller.new_blank(ctx, keys, cancel=cancel)
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
    from voicepilot.windows.office import OfficeController

    keys = Mock()
    probe = Mock(side_effect=OSError("accessibility unavailable"))
    controller = OfficeController(blank_source=probe, apartment=contextlib.nullcontext)
    try:
        assert controller.new_blank(context("excel", "EXCEL.EXE"), keys).status is not ResultStatus.OK
        keys.send_hotkey.assert_not_called()
    finally:
        controller.shutdown()


def test_office_rejects_process_kind_mismatch():
    ctx = replace(context("excel", "EXCEL.EXE"), process_name="notepad.exe")
    keys = Mock()
    executor = WindowsExecutor(AppConfig(), Mock(is_still_target=Mock(return_value=True)), keys=keys)
    try:
        command = RuleParser({}, {}).parse("tạo bảng tính mới", ctx, Mode.COMMAND)
        assert executor.execute(command, ctx).status is not ResultStatus.OK
        keys.send_hotkey.assert_not_called()
    finally:
        executor.shutdown()


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


def test_home_scan_only_invokes_exact_visible_blank_template():
    from voicepilot.windows.office import BLANK_NAMES, _find_blank

    class Node:
        def __init__(self, name, *, state=0, children=()):
            self.name, self.state, self._children = name, state, children
            self.invoke = Mock()

        def children(self):
            return iter(self._children)

    hidden = Node("Blank workbook", state=0x8000)
    other_template = Node("Not blank workbook")
    blank = Node("Blank workbook")
    root = Node("Home", children=[hidden, other_template, blank])
    action = _find_blank(root, BLANK_NAMES["excel.exe"])
    action()
    blank.invoke.assert_called_once_with()
    hidden.invoke.assert_not_called()
    other_template.invoke.assert_not_called()


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


def test_msaa_children_query_accessible_interface_and_clear_native_variants(monkeypatch):
    """Regression: child IDispatch vtables caused native access violations during MSAA traversal."""
    import ctypes

    from voicepilot.windows import office

    adapter = office._Accessible
    root = object.__new__(adapter)
    root._child_id, root._pointer = 0, 101
    root._dispatch = Mock(accChildCount=1)
    vtable = (ctypes.c_void_p * 3)(11, 12, 13)
    vtable_pointer = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    dispatch_pointer = ctypes.addressof(vtable_pointer)
    events = []

    def read_children(pointer, start, count, values, obtained):
        assert pointer == 101
        values[0].vt, values[0].value.pointer = 9, dispatch_pointer
        ctypes.cast(obtained, ctypes.POINTER(ctypes.c_int32)).contents.value = 1

    def query(pointer, iid, output):
        assert pointer == dispatch_pointer and iid == office._IID_ACCESSIBLE
        events.append("query")
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = 202
        return 0

    def make_child(pointer):
        assert pointer == 202
        events.append("wrap_accessible")
        return Mock()

    clear = Mock()
    lib = Mock(AccessibleChildren=Mock(side_effect=read_children), VariantClear=clear)
    monkeypatch.setattr(ctypes, "OleDLL", Mock(return_value=lib), raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *args: lambda address: query, raising=False)
    monkeypatch.setattr(office, "_Accessible", make_child)
    release = Mock()
    monkeypatch.setattr(office, "_release", release)
    assert len(list(adapter.children(root))) == 1
    assert events == ["query", "wrap_accessible"]
    release.assert_called_once_with(202)
    clear.assert_called_once()


@pytest.mark.parametrize("exe,kind", [("WINWORD.EXE", AppKind.WORD), ("EXCEL.EXE", AppKind.EXCEL)])
def test_office_context_classifies_native_full_executable_paths(exe, kind):
    from voicepilot.windows.context import classify_process

    assert classify_process("C:\\Program Files\\Microsoft Office\\root\\Office16\\" + exe) == (kind, None)
