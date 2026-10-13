"""Acceptance harness written before power restore and editable built-ins."""

from dataclasses import replace
from threading import Event

import pytest

from voicepilot.config import AppConfig, load_config, save_config
from voicepilot.contracts import Intent, Mode, ResultStatus

CUSTOM = {"id": "signature", "name": "Signature", "enabled": True,
          "patterns": ["ký tên"], "intent": "DICTATE_TEXT",
          "params": {"text": "Nam"}, "context": "any", "process": ""}


def edited():
    return replace(AppConfig(), input_policy="text", silence_end_ms=1400,
                   custom_commands=[CUSTOM], builtin_patterns={"WIN_SCREENSHOT": ["ghi ảnh màn hình"]})


def test_prompt_reset_keeps_custom_and_other_settings():
    from voicepilot.restore import restored_config

    original = edited()
    result = restored_config(original, "prompts")
    assert result.builtin_patterns == {}
    assert result.custom_commands == [CUSTOM]
    assert result.input_policy == "text" and result.silence_end_ms == 1400
    assert original.builtin_patterns


def test_settings_reset_preserves_detached_custom_only():
    from voicepilot.restore import restored_config

    original = edited()
    result = restored_config(original, "settings")
    assert result.custom_commands == [CUSTOM]
    assert result.custom_commands is not original.custom_commands
    assert result.input_policy == "voice" and result.silence_end_ms == 800
    assert result.builtin_patterns == {}
    assert result.website_aliases["youtube"]


def test_factory_config_has_no_custom_or_overrides():
    from voicepilot.restore import restored_config

    result = restored_config(edited(), "factory")
    assert result.custom_commands == [] and result.builtin_patterns == {}
    assert result.hotkey_listen == "ctrl+alt+v"


def test_builtin_patterns_roundtrip_and_drive_app_parser_and_hotwords(tmp_path):
    from voicepilot.app import _make_parser
    from voicepilot.stt.hotwords import command_phrases

    config = edited()
    save_config(config, tmp_path / "config.toml")
    loaded = load_config(tmp_path / "config.toml")
    assert loaded.builtin_patterns == config.builtin_patterns
    parser = _make_parser(loaded)
    assert parser.parse("ghi ảnh màn hình", None, Mode.COMMAND).intent is Intent.WIN_SCREENSHOT
    assert parser.parse("chụp màn hình", None, Mode.COMMAND).intent is Intent.UNKNOWN
    assert parser.parse("ký tên", None, Mode.COMMAND).custom_id == "signature"
    assert parser.parse("ghi ảnh màn hình", None, Mode.DICTATION).intent is Intent.DICTATE_TEXT
    words = command_phrases({}, {}, [CUSTOM], loaded.builtin_patterns)
    assert "ghi ảnh màn hình" in words and "chụp màn hình" not in words


@pytest.mark.parametrize("patterns", [[], [""], ["   "], [123], ["ảnh {query}"],
                                     ["{bad}"], ["ảnh {"], ["ảnh" * 1000]])
def test_invalid_builtin_edits_rejected(patterns):
    from voicepilot.builtin_commands import validate_overrides

    with pytest.raises(ValueError):
        validate_overrides({"WIN_SCREENSHOT": patterns})


def test_parameterized_builtin_edit_preserves_original_query():
    from voicepilot.intent.parser import RuleParser

    parser = RuleParser({}, {}, builtin_patterns={"BROWSER_SEARCH": ["hỏi web {query}"]})
    cmd = parser.parse("hỏi web Giá vàng hôm nay", None, Mode.COMMAND)
    assert cmd.intent is Intent.BROWSER_SEARCH and cmd.params["query"] == "Giá vàng hôm nay"


def test_off_power_saver_available_in_parser_and_custom():
    from voicepilot.custom_commands import ACTIONS
    from voicepilot.intent.parser import RuleParser

    cmd = RuleParser({}, {}).parse("tắt tiết kiệm pin", None, Mode.COMMAND)
    assert cmd.intent.value == "WIN_POWER_RESTORE"
    assert "WIN_POWER_RESTORE" in ACTIONS


class FakePower:
    def __init__(self):
        self.active = "381b4222-f694-41f0-9685-ff5bb260df2e"
        self.calls = []
        self.reject = False

    def get_active(self):
        return self.active

    def set_active(self, guid):
        self.calls.append(guid)
        if self.reject:
            raise OSError("fixture rejection")
        self.active = guid


def test_power_restore_survives_restart_and_repeated_enable(tmp_path):
    from voicepilot.windows.system_controls import POWER_SAVER_GUID, PowerPlanManager

    api = FakePower()
    original = api.active
    manager = PowerPlanManager(api=api, state_path=tmp_path / "power-state.json")
    assert manager.enable().status is ResultStatus.OK
    assert manager.enable().status is ResultStatus.OK
    assert api.active == POWER_SAVER_GUID
    restarted = PowerPlanManager(api=api, state_path=tmp_path / "power-state.json")
    assert restarted.restore().status is ResultStatus.OK
    assert api.active == original
    assert not (tmp_path / "power-state.json").exists()
    assert restarted.restore().status is ResultStatus.OK


def test_power_failure_keeps_previous_plan_recoverable(tmp_path):
    from voicepilot.windows.system_controls import PowerPlanManager

    api = FakePower()
    manager = PowerPlanManager(api=api, state_path=tmp_path / "power-state.json")
    manager.enable()
    api.reject = True
    assert manager.restore().status is not ResultStatus.OK
    assert (tmp_path / "power-state.json").exists()
    api.reject = False
    assert manager.restore().status is ResultStatus.OK


def test_cancelled_power_does_not_change_plan_or_create_state(tmp_path):
    from voicepilot.windows.system_controls import PowerPlanManager

    api = FakePower()
    cancel = Event()
    cancel.set()
    manager = PowerPlanManager(api=api, state_path=tmp_path / "power-state.json")
    assert manager.enable(cancel).error_code == "cancelled"
    assert not api.calls and not (tmp_path / "power-state.json").exists()


def test_factory_reset_removes_app_data_but_preserves_sibling_files(tmp_path):
    from voicepilot.restore import complete_factory_reset, request_factory_reset

    root = tmp_path / "VoicePilot"
    root.mkdir()
    save_config(edited(), root / "config.toml")
    (root / "logs").mkdir()
    (root / "logs" / "debug.log").write_text("private fixture")
    (root / "stt").mkdir()
    (root / "stt" / "hotwords.txt").write_text("private fixture")
    sibling = tmp_path / "Pictures"
    sibling.mkdir()
    (sibling / "screenshot.png").write_bytes(b"fixture")
    request_factory_reset(root)
    assert complete_factory_reset(root)
    assert sorted(p.name for p in root.iterdir()) == ["config.toml"]
    assert load_config(root / "config.toml").custom_commands == []
    assert (sibling / "screenshot.png").exists()
    assert not complete_factory_reset(root)


def test_factory_reset_does_nothing_without_pending_request(tmp_path):
    from voicepilot.restore import complete_factory_reset

    (tmp_path / "sentinel").write_text("keep")
    assert not complete_factory_reset(tmp_path)
    assert (tmp_path / "sentinel").exists()


def test_factory_reset_rejects_home_root(monkeypatch, tmp_path):
    from voicepilot.restore import request_factory_reset

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    with pytest.raises(ValueError):
        request_factory_reset(tmp_path)


def test_factory_reset_rejects_installation_directory(tmp_path):
    from voicepilot.restore import request_factory_reset

    root = tmp_path / "VoicePilot"
    (root / "models").mkdir(parents=True)
    with pytest.raises(ValueError):
        request_factory_reset(root)
    assert not (root / "factory-reset.pending").exists()


def test_factory_reset_rejects_shared_environment_override(monkeypatch, tmp_path):
    from voicepilot.restore import request_factory_reset

    monkeypatch.setenv("VOICEPILOT_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        request_factory_reset()


def test_factory_reset_rejects_unrecognized_request_before_deletion(tmp_path):
    from voicepilot.restore import complete_factory_reset

    root = tmp_path / "VoicePilot"
    root.mkdir()
    (root / "factory-reset.pending").write_text("unrecognized fixture")
    (root / "sentinel").write_text("keep")
    with pytest.raises(ValueError):
        complete_factory_reset(root)
    assert (root / "sentinel").exists()


@pytest.mark.parametrize("kind", ["prompts", "settings"])
def test_restore_only_persists_on_save_and_preserves_pending_custom(qtbot, monkeypatch, tmp_path, kind):
    from voicepilot.ui.settings import SettingsDialog

    root = tmp_path / "VoicePilot"
    monkeypatch.setenv("VOICEPILOT_CONFIG_DIR", str(root))
    source = edited()
    save_config(source)
    dialog = SettingsDialog(source)
    qtbot.addWidget(dialog)
    dialog._custom._list.setCurrentRow(0)
    dialog._custom._params["text"].setPlainText("Nam mới 👋")
    dialog.restore_draft(kind)
    assert load_config().builtin_patterns == source.builtin_patterns
    assert load_config().custom_commands[0]["params"]["text"] == "Nam"
    dialog.accept()
    assert load_config().builtin_patterns == {}
    assert load_config().custom_commands[0]["params"]["text"] == "Nam mới 👋"


def test_bad_builtin_override_does_not_erase_custom_or_log_private_text(tmp_path, caplog):
    source = edited()
    secret = "Private phrase {invalid}"
    source.builtin_patterns["PPT_SAVE"] = [secret]
    save_config(source, tmp_path / "config.toml")
    loaded = load_config(tmp_path / "config.toml")
    assert loaded.custom_commands == [CUSTOM]
    assert loaded.builtin_patterns == {"WIN_SCREENSHOT": ["ghi ảnh màn hình"]}
    assert secret not in caplog.text


def test_factory_reset_failed_power_restore_leaves_custom_and_files(monkeypatch, tmp_path):
    from voicepilot.contracts import ActionResult
    from voicepilot.restore import complete_factory_reset, request_factory_reset
    from voicepilot.windows import system_controls

    root = tmp_path / "VoicePilot"
    root.mkdir()
    save_config(edited(), root / "config.toml")
    (root / "power-state.json").write_text("fixture")
    request_factory_reset(root)
    monkeypatch.setattr("voicepilot.restore.sys.platform", "win32")
    monkeypatch.setattr(system_controls.PowerPlanManager, "restore",
                        lambda self: ActionResult(ResultStatus.ERROR, "Fixture"))
    with pytest.raises(OSError):
        complete_factory_reset(root)
    assert load_config(root / "config.toml").custom_commands == [CUSTOM]
    assert (root / "factory-reset.pending").exists()


def test_power_restore_preserves_manual_plan_change(tmp_path):
    from voicepilot.windows.system_controls import PowerPlanManager

    api = FakePower()
    manager = PowerPlanManager(api=api, state_path=tmp_path / "power-state.json")
    manager.enable()
    api.active = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
    assert manager.restore().status is ResultStatus.OK
    assert api.active == "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def test_power_without_previous_state_does_not_guess_a_plan(tmp_path):
    from voicepilot.windows.system_controls import POWER_SAVER_GUID, PowerPlanManager

    api = FakePower()
    api.active = POWER_SAVER_GUID
    manager = PowerPlanManager(api=api, state_path=tmp_path / "power-state.json")
    assert manager.restore().error_code == "power_previous_missing"
    assert not api.calls


def test_factory_confirmation_cancel_emits_nothing(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from voicepilot.ui.settings import SettingsDialog

    dialog = SettingsDialog(edited())
    qtbot.addWidget(dialog)
    signals = []
    dialog.factory_reset_requested.connect(lambda: signals.append(True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.No)
    dialog._confirm_factory_reset()
    assert signals == []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes)
    dialog._confirm_factory_reset()
    assert signals == [True]


def test_prompt_and_restore_help_is_readable_on_windows_dark_theme(qtbot):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QLabel, QTabWidget

    from voicepilot.app import _load_style
    from voicepilot.ui.settings import SettingsDialog

    dialog = SettingsDialog(edited())
    dialog.setStyleSheet(_load_style())
    qtbot.addWidget(dialog)
    tabs = dialog.findChild(QTabWidget)
    for index in (1, 3):
        tabs.setCurrentIndex(index)
        for label in tabs.widget(index).findChildren(QLabel):
            label.ensurePolished()
            color = label.palette().color(QPalette.ColorRole.WindowText)
            assert max(color.red(), color.green(), color.blue()) >= 170


@pytest.mark.parametrize("frozen", [False, True])
def test_factory_restart_uses_independent_process_and_preserves_debug(monkeypatch, frozen):
    from unittest.mock import Mock

    from voicepilot import app

    spawn = Mock()
    monkeypatch.setattr(app.subprocess, "Popen", spawn)
    monkeypatch.setattr(app.sys, "frozen", frozen, raising=False)
    app._restart_application(True)
    command = spawn.call_args.args[0]
    assert command[0] == app.sys.executable and "--debug" in command
    assert "--wait-for-exit" in command
    assert ("voicepilot" in command) == (not frozen)
    assert spawn.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_prompt_editor_saves_pending_row_and_cancel_is_detached(qtbot):
    from voicepilot.ui.settings import SettingsDialog

    source = edited()
    dialog = SettingsDialog(source)
    qtbot.addWidget(dialog)
    editor = dialog._builtin
    editor.select_intent("WIN_SCREENSHOT")
    editor._patterns.setPlainText("giữ ảnh màn hình\nchụp màn hình")
    result = dialog.collect()
    assert result.builtin_patterns["WIN_SCREENSHOT"] == ["giữ ảnh màn hình", "chụp màn hình"]
    assert source.builtin_patterns["WIN_SCREENSHOT"] == ["ghi ảnh màn hình"]


def test_restore_draft_reloads_steps_and_prompt_restore_keeps_them(qtbot):
    from voicepilot.ui.settings import SettingsDialog

    source = replace(edited(), volume_step_percent=55, brightness_step_percent=30)
    dialog = SettingsDialog(source)
    qtbot.addWidget(dialog)
    dialog.restore_draft("prompts")
    assert dialog._builtin.steps() == {"volume": 55, "brightness": 30}
    dialog.restore_draft("settings")
    assert dialog._builtin.steps() == {"volume": 20, "brightness": 20}
    result = dialog.collect()
    assert result.volume_step_percent == 20 and result.brightness_step_percent == 20
    assert source.volume_step_percent == 55


@pytest.mark.parametrize("kind", ["prompts", "settings"])
def test_settings_restore_is_draft_until_save_and_keeps_custom(qtbot, kind):
    from voicepilot.ui.settings import SettingsDialog

    source = edited()
    dialog = SettingsDialog(source)
    qtbot.addWidget(dialog)
    dialog.restore_draft(kind)
    result = dialog.collect()
    assert result.custom_commands == [CUSTOM] and result.builtin_patterns == {}
    assert result.input_policy == ("text" if kind == "prompts" else "voice")
    assert source.builtin_patterns
