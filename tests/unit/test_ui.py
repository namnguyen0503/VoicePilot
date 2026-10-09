"""Overlay, settings dialog and tray helpers: focus, visibility, hiding and config round-trip."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from voicepilot.config import AppConfig, load_config
from voicepilot.contracts import Mode, SttBackendMode
from voicepilot.ui.overlay import READY_TEXT, Overlay
from voicepilot.ui.settings import SettingsDialog
from voicepilot.ui.tray import _commands_html, pretty_hotkey


def test_overlay_never_takes_focus(qtbot: QtBot) -> None:
    overlay = Overlay()
    qtbot.addWidget(overlay)
    flags = overlay.windowFlags()
    for flag in (
        Qt.WindowType.Tool,
        Qt.WindowType.FramelessWindowHint,
        Qt.WindowType.WindowStaysOnTopHint,
        Qt.WindowType.WindowDoesNotAcceptFocus,
    ):
        assert (flags & flag) == flag
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_overlay_shows_while_listening_and_hides_after_the_turn(qtbot: QtBot) -> None:
    overlay = Overlay(auto_hide_ms=50)
    qtbot.addWidget(overlay)
    overlay.on_state("LISTENING", "Đang nghe…")
    qtbot.waitUntil(overlay.isVisible, timeout=2000)

    overlay.on_state("DONE", "Đã chuyển slide.")
    qtbot.waitUntil(lambda: not overlay.isVisible(), timeout=2000)


def test_pinned_overlay_stays_visible_and_shows_ready_text(qtbot: QtBot) -> None:
    overlay = Overlay(auto_hide_ms=30)
    qtbot.addWidget(overlay)
    overlay.set_pinned(True)
    assert overlay.isVisible()

    overlay.on_state("DONE", "Đã chuyển slide.")
    qtbot.waitUntil(lambda: overlay._status.text() == READY_TEXT, timeout=2000)
    assert overlay.isVisible()


def test_cancel_button_is_shown_only_during_a_turn(qtbot: QtBot) -> None:
    overlay = Overlay()
    qtbot.addWidget(overlay)
    overlay.on_state("RECOGNIZING", "Đang nhận dạng…")
    assert not overlay._cancel.isHidden()
    with qtbot.waitSignal(overlay.cancel_requested, timeout=2000):
        overlay._cancel.click()
    overlay.on_state("DONE", "Xong")
    assert overlay._cancel.isHidden()


def test_hidden_temporarily_restores_visibility_on_the_gui_thread(qtbot: QtBot) -> None:
    overlay = Overlay()
    qtbot.addWidget(overlay)
    overlay.show_bar()
    qtbot.waitUntil(overlay.isVisible, timeout=2000)

    with overlay.hidden_temporarily():
        assert not overlay.isVisible()
    assert overlay.isVisible()


def test_hidden_temporarily_works_from_a_worker_thread(qtbot: QtBot) -> None:
    overlay = Overlay()
    qtbot.addWidget(overlay)
    overlay.show_bar()
    qtbot.waitUntil(overlay.isVisible, timeout=2000)

    seen: dict[str, object] = {}

    def worker() -> None:
        with overlay.hidden_temporarily():
            seen["during"] = overlay._gui.run(overlay.isVisible)
        seen["done"] = True

    thread = threading.Thread(target=worker)
    thread.start()
    qtbot.waitUntil(lambda: seen.get("done") is True, timeout=5000)
    thread.join(timeout=5)

    assert seen["during"] is False
    assert overlay.isVisible()


def test_mode_chip_follows_the_mode(qtbot: QtBot) -> None:
    overlay = Overlay()
    qtbot.addWidget(overlay)
    overlay.set_mode(Mode.DICTATION)
    assert overlay._mode_chip.text() == "Chính tả"
    overlay.set_mode(Mode.COMMAND)
    assert overlay._mode_chip.text() == "Ra lệnh"


def test_settings_round_trip_writes_config(qtbot: QtBot, config_home: Path) -> None:
    dialog = SettingsDialog(replace(AppConfig(), overlay_pos=(5, 6)))
    qtbot.addWidget(dialog)
    dialog._listen_edit.setText("Ctrl+Shift+Space")
    dialog._mode_edit.setText("ctrl+alt+m")
    dialog._stt_combo.setCurrentIndex(dialog._stt_combo.findData(SttBackendMode.OFFLINE))
    dialog._tts_check.setChecked(False)
    dialog._silence_spin.setValue(1200)
    dialog._threads_spin.setValue(4)

    with qtbot.waitSignal(dialog.settings_changed, timeout=2000) as blocker:
        dialog.accept()

    assert blocker.args[0].hotkey_listen == "ctrl+shift+space"
    loaded = load_config()
    assert loaded.hotkey_listen == "ctrl+shift+space"
    assert loaded.hotkey_mode == "ctrl+alt+m"
    assert loaded.stt_mode is SttBackendMode.OFFLINE
    assert loaded.tts_enabled is False
    assert loaded.silence_end_ms == 1200
    assert loaded.gipformer_threads == 4
    assert loaded.overlay_pos == (5, 6)


def test_settings_rejects_empty_or_duplicate_hotkeys(qtbot: QtBot, config_home: Path) -> None:
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    dialog._listen_edit.setText("")
    dialog.accept()
    assert dialog._error.text() != ""

    dialog._listen_edit.setText("ctrl+alt+d")
    dialog._mode_edit.setText("ctrl+alt+d")
    dialog.accept()
    assert "khác nhau" in dialog._error.text()
    assert not (config_home / "config.toml").exists()


def test_pretty_hotkey_formats_modifier_names() -> None:
    assert pretty_hotkey("ctrl+alt+space") == "Ctrl+Alt+Space"
    assert pretty_hotkey("ctrl+alt+d") == "Ctrl+Alt+D"


def test_command_list_is_built_from_commands_toml() -> None:
    html = _commands_html()
    assert "PPT_NEXT_SLIDE" in html
    assert "Thử nói:" in html
