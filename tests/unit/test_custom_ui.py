"""Custom command management must be usable without editing configuration files."""

from voicepilot.config import AppConfig
from voicepilot.ui.custom_commands import CustomCommandsWidget
from voicepilot.ui.settings import SettingsDialog


def test_editor_save_preview_duplicate_remove_and_roundtrip(qtbot, tmp_path):
    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    widget.new_command()
    widget._name.setText("Hỏi Google")
    widget._patterns.setPlainText("hỏi google về {topic}\ntra giá {topic}")
    widget._action.setCurrentIndex(widget._action.findData("BROWSER_SEARCH"))
    widget._params["query"].setText("{topic}")
    assert widget.save_command()
    widget._try_text.setText("hỏi google về Giá vàng hôm nay")
    widget.preview()
    assert "Giá vàng hôm nay" in widget._preview.text()
    widget.duplicate_command()
    assert len(widget.commands()) == 2
    widget.remove_command()
    assert len(widget.commands()) == 1
    path = tmp_path / "custom.json"
    widget.export_to(path)
    other = CustomCommandsWidget([])
    qtbot.addWidget(other)
    other.import_from(path)
    assert other.commands() == widget.commands()


def test_select_command_by_id_selects_row_and_rejects_unknown_id(qtbot):
    widget = CustomCommandsWidget([CUSTOM_ITEM, dict(CUSTOM_ITEM, id="second", name="Second")])
    qtbot.addWidget(widget)
    assert widget.select_command("second")
    assert widget._list.currentRow() == 1
    assert widget._name.text() == "Second"
    assert not widget.select_command("missing")
    assert widget._list.currentRow() == 1


CUSTOM_ITEM = {"id": "first", "name": "First", "enabled": True, "patterns": ["ký tên"],
               "intent": "DICTATE_TEXT", "params": {"text": "Nam"}, "context": "any", "process": ""}


def test_settings_collect_preserves_custom_commands(qtbot):
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    assert isinstance(dialog._custom, CustomCommandsWidget)
    assert dialog.collect().custom_commands == []


def test_invalid_import_keeps_existing_list(qtbot, tmp_path):
    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    path = tmp_path / "bad.json"
    path.write_text('[{"id":"bad"}]')
    try:
        widget.import_from(path)
    except ValueError:
        pass
    assert widget.commands() == []


def test_unsaved_valid_form_collected_and_disabled_persisted(qtbot):
    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    widget._name.setText("Select all")
    widget._patterns.setPlainText("chọn toàn bộ")
    widget._action.setCurrentIndex(widget._action.findData("WIN_HOTKEY"))
    widget._params["keys"].setText("ctrl+a")
    widget._enabled.setChecked(False)
    assert widget.commands()[0]["enabled"] is False
    assert widget.commands()[0]["params"] == {"keys": "ctrl+a"}


def test_invalid_draft_prevents_switching_or_saving(qtbot):
    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    widget._name.setText("Invalid draft")
    widget.new_command()
    assert widget._name.text() == "Invalid draft"
    assert widget._error.text()


def test_import_conflict_ids_preserved_and_invalid_payload_is_transactional(qtbot, tmp_path):
    import json

    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    widget._name.setText("A")
    widget._patterns.setPlainText("câu riêng")
    widget._params["query"].setText("query")
    assert widget.save_command()
    previous = widget.commands()
    path = tmp_path / "import.json"
    path.write_text(json.dumps({"version": 1, "commands": [{"id": "bad"}]}), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError):
        widget.import_from(path)
    assert widget.commands() == previous
    widget.export_to(path)
    widget.import_from(path)
    assert widget.commands() == previous


def test_settings_cancel_leaves_original_config_untouched(qtbot):
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._custom._name.setText("Temporary")
    dialog._custom._patterns.setPlainText("câu riêng")
    dialog._custom._params["query"].setText("query")
    assert dialog._custom.save_command()
    dialog.reject()
    assert config.custom_commands == []


def test_multiline_signature_editor_keeps_newlines(qtbot):
    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    widget._name.setText("Signature")
    widget._patterns.setPlainText("ký tên")
    widget._action.setCurrentIndex(widget._action.findData("DICTATE_TEXT"))
    widget._params["text"].setPlainText("Nam\nVoicePilot 👋")
    assert widget.commands()[0]["params"] == {"text": "Nam\nVoicePilot 👋"}


def test_enabled_checkbox_remains_readable_on_custom_form(qtbot, qapp):
    from PySide6.QtGui import QPalette

    from voicepilot.app import _load_style

    previous = qapp.styleSheet()
    qapp.setStyleSheet(_load_style())
    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    widget.show()
    qapp.processEvents()
    try:
        foreground = widget._enabled.palette().color(QPalette.ColorRole.WindowText)
        background = widget._enabled.parentWidget().palette().color(QPalette.ColorRole.Window)

        def luminance(color):
            rgb = [color.redF(), color.greenF(), color.blueF()]
            values = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
            return sum(x * weight for x, weight in zip(values, [0.2126, 0.7152, 0.0722], strict=True))

        a, b = sorted([luminance(foreground), luminance(background)])
        assert (b + 0.05) / (a + 0.05) >= 4.5
    finally:
        qapp.setStyleSheet(previous)



def test_duplicate_long_name_is_valid_and_disabled(qtbot):
    from voicepilot.custom_commands import validate_commands
    widget = CustomCommandsWidget([])
    qtbot.addWidget(widget)
    widget._name.setText("A" * 120)
    widget._patterns.setPlainText("câu riêng")
    widget._params["query"].setText("gold")
    assert widget.save_command()
    widget.duplicate_command()
    items = validate_commands(widget.commands())
    assert len(items) == 2 and not items[1]["enabled"]
