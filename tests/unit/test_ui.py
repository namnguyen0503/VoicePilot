"""Overlay, settings dialog and tray helpers: focus, visibility, hiding and config round-trip."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton
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


def test_settings_can_enable_auto_stop_from_disabled_config(qtbot: QtBot, config_home: Path) -> None:
    dialog = SettingsDialog(AppConfig(vad_enabled=False))
    qtbot.addWidget(dialog)
    assert not dialog._vad_check.isChecked()
    assert not dialog._silence_spin.isEnabled()
    dialog._vad_check.setChecked(True)
    assert dialog._silence_spin.isEnabled()
    with qtbot.waitSignal(dialog.settings_changed, timeout=2000):
        dialog.accept()
    assert load_config().vad_enabled is True


def test_settings_can_disable_auto_stop(qtbot: QtBot, config_home: Path) -> None:
    dialog = SettingsDialog(AppConfig(vad_enabled=True))
    qtbot.addWidget(dialog)
    dialog._vad_check.setChecked(False)
    with qtbot.waitSignal(dialog.settings_changed, timeout=2000):
        dialog.accept()
    assert load_config().vad_enabled is False


def _dialog(qtbot: QtBot, config: AppConfig | None = None) -> SettingsDialog:
    dialog = SettingsDialog(config or AppConfig())
    qtbot.addWidget(dialog)
    return dialog


def _result_keys(dialog: SettingsDialog) -> list[str]:
    return [dialog._results.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog._results.count())]


def test_builtin_tab_is_named_lenh_co_san(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    assert [dialog._tabs.tabText(i) for i in range(4)] == [
        "Chung", "Lệnh có sẵn", "Lệnh tùy chỉnh", "Khôi phục",
    ]


def test_step_option_is_shown_only_for_volume_and_brightness(qtbot: QtBot) -> None:
    builtin = _dialog(qtbot)._builtin
    for name in ("WIN_VOLUME_UP", "WIN_VOLUME_DOWN", "WIN_BRIGHTNESS_UP", "WIN_BRIGHTNESS_DOWN"):
        builtin.select_intent(name)
        assert not builtin._step_box.isHidden(), name
    builtin.select_intent("WIN_SCREENSHOT")
    assert builtin._step_box.isHidden()
    builtin.select_intent("WIN_VOLUME_UP")
    assert "Dùng chung cho tăng và giảm âm lượng" in builtin._step_caption.text()
    assert builtin._step_spin.suffix() == " %"
    assert builtin._step_spin.minimum() == 1 and builtin._step_spin.maximum() == 100


def test_volume_step_is_shared_by_up_and_down(qtbot: QtBot) -> None:
    builtin = _dialog(qtbot)._builtin
    builtin.select_intent("WIN_VOLUME_UP")
    assert builtin._step_spin.value() == 20
    builtin._step_spin.setValue(35)
    builtin.select_intent("WIN_VOLUME_DOWN")
    assert builtin._step_spin.value() == 35
    builtin._step_spin.setValue(10)
    builtin.select_intent("WIN_VOLUME_UP")
    assert builtin._step_spin.value() == 10
    builtin.select_intent("WIN_BRIGHTNESS_UP")
    assert builtin._step_spin.value() == 20
    assert builtin.steps() == {"volume": 10, "brightness": 20}


def test_save_writes_volume_and_brightness_steps(qtbot: QtBot, config_home: Path) -> None:
    dialog = _dialog(qtbot)
    builtin = dialog._builtin
    builtin.select_intent("WIN_VOLUME_DOWN")
    builtin._step_spin.setValue(30)
    builtin.select_intent("WIN_BRIGHTNESS_UP")
    builtin._step_spin.setValue(15)
    with qtbot.waitSignal(dialog.settings_changed, timeout=2000):
        dialog.accept()
    loaded = load_config()
    assert loaded.volume_step_percent == 30
    assert loaded.brightness_step_percent == 15


def test_per_command_restore_resets_its_phrasings_and_step(qtbot: QtBot) -> None:
    config = replace(AppConfig(), volume_step_percent=50, brightness_step_percent=40,
                     builtin_patterns={"WIN_VOLUME_UP": ["to tiếng lên"]})
    builtin = _dialog(qtbot, config)._builtin
    builtin.select_intent("WIN_VOLUME_UP")
    assert builtin._patterns.toPlainText() == "to tiếng lên"
    assert builtin._step_spin.value() == 50
    button = next(b for b in builtin.findChildren(QPushButton)
                  if b.text() == "Khôi phục mặc định của lệnh này")
    button.click()
    assert "tăng âm lượng" in builtin._patterns.toPlainText()
    assert builtin.steps() == {"volume": 20, "brightness": 40}
    assert "WIN_VOLUME_UP" not in builtin.overrides()


def test_reset_all_resets_steps_and_phrasings(qtbot: QtBot) -> None:
    config = replace(AppConfig(), volume_step_percent=50, brightness_step_percent=40,
                     builtin_patterns={"WIN_BRIGHTNESS_UP": ["sáng hơn nữa"]})
    builtin = _dialog(qtbot, config)._builtin
    builtin.reset_all()
    assert builtin.steps() == {"volume": 20, "brightness": 20}
    assert builtin.overrides() == {}


def test_search_enter_opens_builtin_command_and_selects_it(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    qtbot.keyClicks(dialog._search, "tang am luong")
    assert dialog._stack.currentIndex() == 1
    assert _result_keys(dialog)[0] == "builtin:WIN_VOLUME_UP"
    qtbot.keyClick(dialog._search, Qt.Key.Key_Return)
    assert dialog._search.text() == ""
    assert dialog._stack.currentIndex() == 0
    assert dialog._tabs.currentWidget() is dialog._builtin
    names = dialog._builtin.intents()
    assert dialog._builtin._list.currentRow() == names.index("WIN_VOLUME_UP")


def test_search_result_click_opens_custom_command(qtbot: QtBot) -> None:
    from voicepilot.ui.custom_commands import CustomCommandsWidget

    source = CustomCommandsWidget([])
    qtbot.addWidget(source)
    source.new_command()
    source._name.setText("Mở nhạc")
    source._patterns.setPlainText("mở nhạc")
    source._action.setCurrentIndex(source._action.findData("BROWSER_SEARCH"))
    source._params["query"].setText("nhạc")
    assert source.save_command()
    commands = source.commands()
    dialog = _dialog(qtbot, replace(AppConfig(), custom_commands=commands))
    qtbot.keyClicks(dialog._search, "mo nhac")
    assert _result_keys(dialog) == [f"custom:{commands[0]['id']}"]
    dialog._results.itemClicked.emit(dialog._results.item(0))
    assert dialog._tabs.currentWidget() is dialog._custom
    assert dialog._custom._list.currentRow() == 0
    assert dialog._custom._name.text() == "Mở nhạc"


def test_search_step_item_focuses_the_spin_box(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    qtbot.keyClicks(dialog._search, "muc chinh do sang")
    assert _result_keys(dialog) == ["step:brightness"]
    qtbot.keyClick(dialog._search, Qt.Key.Key_Return)
    builtin = dialog._builtin
    assert dialog._tabs.currentWidget() is builtin
    assert builtin.intents()[builtin._list.currentRow()] == "WIN_BRIGHTNESS_UP"
    assert not builtin._step_box.isHidden()


def test_search_restore_and_general_items_switch_tabs(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    qtbot.keyClicks(dialog._search, "factory reset")
    assert _result_keys(dialog) == ["restore:factory"]
    qtbot.keyClick(dialog._search, Qt.Key.Key_Return)
    assert dialog._tabs.currentIndex() == 3

    qtbot.keyClicks(dialog._search, "doc phan hoi")
    assert _result_keys(dialog)[0] == "general:tts"
    dialog._results.itemActivated.emit(dialog._results.item(0))
    assert dialog._tabs.currentIndex() == 0
    assert dialog._search.text() == ""


def test_search_general_row_focuses_its_widget(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    dialog.show()
    qtbot.waitExposed(dialog)
    qtbot.keyClicks(dialog._search, "phim tat")
    qtbot.keyClick(dialog._search, Qt.Key.Key_Return)
    assert dialog._tabs.currentIndex() == 0
    qtbot.waitUntil(lambda: QApplication.focusWidget() is dialog._listen_edit, timeout=2000)


def test_search_no_results_message_and_escape_clears(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    qtbot.keyClicks(dialog._search, "zzqx")
    assert dialog._stack.currentIndex() == 1
    assert not dialog._no_results.isHidden()
    assert "Không tìm thấy cài đặt phù hợp." in dialog._no_results.text()
    qtbot.keyClick(dialog._search, Qt.Key.Key_Escape)
    assert dialog._search.text() == ""
    assert dialog._stack.currentIndex() == 0


def test_search_down_arrow_moves_into_results(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    dialog.show()
    qtbot.waitExposed(dialog)
    qtbot.keyClicks(dialog._search, "tts")
    qtbot.keyClick(dialog._search, Qt.Key.Key_Down)
    qtbot.waitUntil(lambda: QApplication.focusWidget() is dialog._results, timeout=2000)
    assert dialog._results.currentRow() == 0


def test_ctrl_f_focuses_search_box(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    dialog.show()
    qtbot.waitExposed(dialog)
    qtbot.keyClick(dialog._tabs, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: QApplication.focusWidget() is dialog._search, timeout=2000)
