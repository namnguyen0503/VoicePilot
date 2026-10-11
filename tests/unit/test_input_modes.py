"""Voice/text acceptance harness, specified before implementation."""

from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt

from voicepilot.config import AppConfig, load_config, save_config
from voicepilot.contracts import (
    ActionResult,
    AppKind,
    AudioClip,
    Intent,
    Mode,
    ResultStatus,
    Transcript,
    WindowContext,
)
from voicepilot.controller import Controller
from voicepilot.intent.parser import RuleParser

CTX = WindowContext(
    hwnd=42, pid=7, process_name="notepad.exe", title="Fixture", app_kind=AppKind.OTHER, browser=None
)


class Recorder:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = 0
        self.cancel = None

    def record(self, turn_id, mode, stop_event, cancel_event):
        self.calls += 1
        self.cancel = cancel_event
        self.entered.set()
        assert self.release.wait(3)
        return AudioClip(samples=b"\x01\x00" * 1600, turn_id=turn_id)


class Transcriber:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.text = "mở youtube"
        self.modes = []

    def for_mode(self, mode):
        self.modes.append(mode)
        return self

    def transcribe(self, clip, is_valid, cancel_event):
        self.entered.set()
        assert self.release.wait(3)
        return Transcript(self.text, "fake", 1, turn_id=clip.turn_id)


@pytest.fixture
def rig(qtbot):
    recorder, transcriber = Recorder(), Transcriber()
    executor = Mock()
    executor.execute.return_value = ActionResult(ResultStatus.OK, "Xong")
    provider = Mock()
    provider.capture.return_value = CTX
    provider.is_still_target.return_value = True
    controller = Controller(
        AppConfig(),
        recorder,
        transcriber,
        RuleParser({"youtube": "https://www.youtube.com"}, {}),
        executor,
        Mock(),
        provider,
        hold_ms=5,
    )
    results = []
    controller.result_ready.connect(results.append)
    yield controller, recorder, transcriber, executor, provider, results
    recorder.release.set()
    transcriber.release.set()
    controller.shutdown()


@pytest.mark.parametrize("initial", [Mode.COMMAND, Mode.DICTATION])
def test_input_switch_during_capture_keeps_audio_and_uses_latest_mode(rig, qtbot, initial):
    c, r, t, e, _, results = rig
    if initial is Mode.DICTATION:
        c.toggle_mode()
    c.toggle_listen()
    qtbot.waitUntil(r.entered.is_set)
    c.toggle_mode()
    expected = Mode.DICTATION if initial is Mode.COMMAND else Mode.COMMAND
    assert c.mode is expected
    r.release.set()
    qtbot.waitUntil(lambda: bool(results))
    assert r.calls == 1
    assert t.modes == [expected]
    command = e.execute.call_args.args[0]
    assert command.intent is (Intent.DICTATE_TEXT if expected is Mode.DICTATION else Intent.BROWSER_OPEN_SITE)
    if expected is Mode.DICTATION:
        assert command.params == {"text": "mở youtube"}


def test_input_switch_after_recognition_started_cancels_old_result(rig, qtbot):
    c, r, t, e, _, results = rig
    t.release.clear()
    r.release.set()
    c.toggle_listen()
    qtbot.waitUntil(t.entered.is_set)
    c.toggle_mode()
    assert c.mode is Mode.DICTATION
    t.release.set()
    c._last_future.result(3)
    qtbot.wait(20)
    e.execute.assert_not_called()
    assert results == []


def test_input_text_uses_parser_without_microphone_or_stt(rig, qtbot):
    c, r, t, e, _, results = rig
    c.set_input_channel("text")
    assert c.submit_text("giá vàng hôm nay bao nhiêu", CTX)
    qtbot.waitUntil(lambda: bool(results))
    assert r.calls == 0
    assert not t.entered.is_set()
    command = e.execute.call_args.args[0]
    assert command.intent is Intent.BROWSER_SEARCH
    assert command.params["query"] == "giá vàng hôm nay bao nhiêu"
    assert c.history[0][2] == "giá vàng hôm nay bao nhiêu"


def test_input_text_blocks_voice_empty_and_duplicate_submissions(rig, qtbot):
    c, r, _, e, _, results = rig
    assert not c.submit_text("mở youtube", CTX)
    c.set_input_channel("text")
    for _ in range(4):
        c.toggle_listen()
    assert r.calls == 0
    assert not c.submit_text("   ", CTX)
    assert c.submit_text("mở youtube", CTX)
    assert not c.submit_text("mở youtube", CTX)
    qtbot.waitUntil(lambda: bool(results))
    assert e.execute.call_count == 1


def test_input_text_cancels_late_voice_before_new_command(rig, qtbot):
    c, r, t, e, _, results = rig
    t.release.clear()
    r.release.set()
    c.toggle_listen()
    qtbot.waitUntil(t.entered.is_set)
    old = c._last_future
    c.set_input_channel("text")
    assert r.cancel.is_set()
    assert c.submit_text("tìm giá vàng", CTX)
    qtbot.waitUntil(lambda: bool(results))
    t.release.set()
    old.result(3)
    qtbot.wait(20)
    assert e.execute.call_count == 1
    assert e.execute.call_args.args[0].params["query"] == "giá vàng"
    assert len(c.history) == 1


def test_input_text_dictation_preserves_text_and_checks_focus(rig, qtbot):
    c, _, _, e, p, results = rig
    c.set_input_channel("text")
    c.toggle_mode()
    p.is_still_target.return_value = False
    assert c.submit_text("mở youtube 👋", CTX)
    qtbot.waitUntil(lambda: bool(results))
    e.execute.assert_not_called()
    assert results[0].status is ResultStatus.NEEDS_RETRY
    p.is_still_target.return_value = True
    assert c.submit_text("mở youtube 👋", CTX)
    qtbot.waitUntil(lambda: len(results) == 2)
    assert e.execute.call_args.args[0].params == {"text": "mở youtube 👋"}


@pytest.mark.parametrize("policy,expected", [("voice", "voice"), ("remember", "text"), ("text", "text")])
def test_input_preferences_roundtrip_and_session_choice(tmp_path, policy, expected):
    from voicepilot.config import opening_input_channel

    config = replace(AppConfig(), input_policy=policy, last_input_channel="text")
    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)
    assert opening_input_channel(loaded) == expected
    assert loaded.input_policy == policy
    assert loaded.last_input_channel == "text"


def test_input_preferences_invalid_values_fall_back(tmp_path):
    from voicepilot.config import opening_input_channel

    path = tmp_path / "config.toml"
    path.write_text('input_policy="invalid"\nlast_input_channel="invalid"', encoding="utf-8")
    config = load_config(path)
    assert opening_input_channel(config) == "voice"
    assert config.last_input_channel == "voice"


def test_input_composer_enter_and_send_share_busy_guard(qtbot):
    from voicepilot.ui.text_input import TextInputWindow

    window = TextInputWindow()
    qtbot.addWidget(window)
    sent = []
    window.submitted.connect(sent.append)
    window.show()
    window._edit.setText("mở youtube")
    qtbot.keyClick(window._edit, Qt.Key.Key_Return)
    window._send.click()
    assert sent == ["mở youtube"]
    window.on_state("DONE", "Xong")
    window._edit.setText("tìm giá vàng")
    window._send.click()
    assert sent == ["mở youtube", "tìm giá vàng"]


def test_input_overlay_mode_button_and_text_tab_are_nonactivating(qtbot):
    from voicepilot.ui.overlay import Overlay

    overlay = Overlay()
    qtbot.addWidget(overlay)
    modes, channels = [], []
    overlay.mode_requested.connect(lambda: modes.append(True))
    overlay.input_requested.connect(channels.append)
    overlay._mode_chip.click()
    overlay._input_tabs.setCurrentIndex(1)
    assert modes == [True]
    assert channels == ["text"]
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus


def test_input_settings_policy_can_be_saved(qtbot, config_home):
    from voicepilot.ui.settings import SettingsDialog

    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    dialog._input_policy_combo.setCurrentIndex(dialog._input_policy_combo.findData("remember"))
    dialog.accept()
    assert load_config().input_policy == "remember"


def test_input_voice_capture_cannot_overlap_a_cancelled_stream(rig, qtbot):
    c, r, _, _, _, _ = rig
    c.toggle_listen()
    qtbot.waitUntil(r.entered.is_set)
    first = c._last_future
    c.set_input_channel("text")
    c.set_input_channel("voice")
    c.toggle_listen()
    qtbot.wait(30)
    assert r.calls == 1  # Old stream is closing, so the new worker must wait.
    r.release.set()
    first.result(3)
    qtbot.waitUntil(lambda: r.calls == 2)


def test_input_manual_navigation_search_and_configured_shortcuts(qtbot):
    from voicepilot.ui.manual import UserManualWindow

    config = replace(AppConfig(), hotkey_listen="ctrl+alt+v", hotkey_mode="ctrl+alt+m")
    window = UserManualWindow(config)
    qtbot.addWidget(window)
    assert any("Ctrl+Alt+V" in page for _, page in window._pages)
    assert any("Ctrl+Alt+M" in page for _, page in window._pages)
    window._search.setText("tiếng Việt")
    assert any(not window._sections.item(i).isHidden() for i in range(window._sections.count()))
    window._search.setText("no-such-manual-section")
    assert "Không tìm thấy" in window._browser.toPlainText()
    window._search.clear()
    assert window._sections.currentRow() >= 0


@pytest.mark.parametrize("foreground,target_pid,expected", [(10, 7, True), (99, 7, False), (10, 999, False)])
def test_input_focus_restore_rejects_other_windows_and_reused_handles(
    monkeypatch, foreground, target_pid, expected
):
    import ctypes

    from voicepilot.windows import context

    own_pid = context.os.getpid()
    states = iter([(foreground, own_pid if foreground == 10 else 800), (42, 7)])
    monkeypatch.setattr(context, "_foreground", lambda: next(states))
    api = Mock()

    def get_pid(hwnd, out):
        ctypes.cast(out, ctypes.POINTER(ctypes.c_uint32)).contents.value = target_pid

    api.GetWindowThreadProcessId.side_effect = get_pid
    api.IsWindow.return_value = 1
    api.SetForegroundWindow.return_value = 1
    monkeypatch.setattr(context, "_user32", lambda: api)
    assert context.restore_text_target(CTX, 10) is expected
    assert api.SetForegroundWindow.call_count == int(expected)


def test_input_manual_has_readable_dark_document_background(qtbot, qapp):
    from PySide6.QtCore import QPoint

    from voicepilot.app import _load_style
    from voicepilot.ui.manual import UserManualWindow

    previous = qapp.styleSheet()
    try:
        qapp.setStyleSheet(_load_style())
        window = UserManualWindow(AppConfig())
        qtbot.addWidget(window)
        window.show()
        window._search.setText("tiết kiệm pin")
        qtbot.wait(20)
        viewport = window._browser.viewport()
        point = viewport.mapTo(window, QPoint(viewport.width() - 20, viewport.height() - 20))
        pixel = window.grab().toImage().pixelColor(point)
        assert max(pixel.redF(), pixel.greenF(), pixel.blueF()) < 0.3
    finally:
        qapp.setStyleSheet(previous)


def test_input_composer_keeps_input_and_send_inside_available_screen(qtbot):
    from voicepilot.ui.text_input import TextInputWindow

    window = TextInputWindow()
    qtbot.addWidget(window)
    window.move(10000, 10000)
    window.open_for_input("Windows")
    assert window.screen().availableGeometry().contains(window.frameGeometry())
