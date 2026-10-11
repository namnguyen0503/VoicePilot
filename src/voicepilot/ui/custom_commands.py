"""Custom command editor with dry-run preview and transactional JSON import/export."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from voicepilot.contracts import AppKind, Mode, WindowContext
from voicepilot.custom_commands import ACTIONS, CONTEXTS, validate_commands


class CustomCommandsWidget(QWidget):
    """Edits detached commands; settings Save persists them, Cancel discards them."""

    def __init__(self, commands: list[dict], parent: QWidget | None = None, *, config=None) -> None:
        super().__init__(parent)
        self.setObjectName("customCommands")
        self._config = config
        self._commands = validate_commands(commands)
        self._editing_id = None
        self._dirty = False
        self._loading = False
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._select)
        self._name = QLineEdit()
        self._enabled = QCheckBox("Bật lệnh này")
        self._patterns = QPlainTextEdit()
        self._patterns.setPlaceholderText("hỏi google về {noidung}\ntra giá {noidung}")
        self._patterns.setMaximumHeight(110)
        self._action = QComboBox()
        for key, (label, _) in ACTIONS.items():
            self._action.addItem(label, key)
        self._context = QComboBox()
        for key, label in CONTEXTS.items():
            self._context.addItem(label, key)
        self._process = QLineEdit()
        self._process.setPlaceholderText("Tùy chọn: notepad.exe, winword.exe…")
        form = QFormLayout()
        form.addRow("Tên lệnh", self._name)
        form.addRow("", self._enabled)
        form.addRow("Cách gọi (mỗi dòng một cách)", self._patterns)
        form.addRow("Hành động", self._action)
        self._params = {}
        self._param_labels = {}
        for _, fields in ACTIONS.values():
            for key, label in fields.items():
                if key in self._params:
                    continue
                edit = QPlainTextEdit() if key == "text" else QLineEdit()
                if isinstance(edit, QPlainTextEdit):
                    edit.setMaximumHeight(90)
                caption = QLabel(label)
                self._params[key] = edit
                self._param_labels[key] = caption
                form.addRow(caption, edit)
                edit.textChanged.connect(self._changed)
        self._browse = QPushButton("Chọn ứng dụng…")
        self._browse.clicked.connect(self._browse_app)
        form.addRow("", self._browse)
        form.addRow("Ngữ cảnh", self._context)
        form.addRow("Chỉ trong ứng dụng", self._process)
        self._action.currentIndexChanged.connect(self._action_changed)
        self._name.textChanged.connect(self._changed)
        self._enabled.toggled.connect(self._changed)
        self._patterns.textChanged.connect(self._changed)
        self._context.currentIndexChanged.connect(self._changed)
        self._process.textChanged.connect(self._changed)
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setObjectName("settingsError")
        self._try_text = QLineEdit()
        self._try_text.setPlaceholderText("Nhập câu nói để kiểm tra")
        self._try_context = QComboBox()
        for key, label in CONTEXTS.items():
            if key != "any":
                self._try_context.addItem(label, key)
        self._try_process = QLineEdit()
        self._try_process.setPlaceholderText("Tên exe khi thử, ví dụ notepad.exe")
        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pane = QWidget()
        pane.setObjectName("customCommandForm")
        right = QVBoxLayout(pane)
        right.addLayout(form)
        save = QPushButton("Áp dụng lệnh vào danh sách")
        save.clicked.connect(self.save_command)
        right.addWidget(save)
        right.addWidget(self._error)
        right.addWidget(QLabel("Thử khớp câu (chỉ xem kết quả, không thực thi)"))
        right.addWidget(self._try_text)
        right.addWidget(self._try_context)
        right.addWidget(self._try_process)
        preview = QPushButton("Kiểm tra câu nói")
        preview.clicked.connect(self.preview)
        right.addWidget(preview)
        right.addWidget(self._preview)
        right.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(pane)
        split = QSplitter()
        split.addWidget(self._list)
        split.addWidget(scroll)
        split.setStretchFactor(1, 3)
        split.setSizes([220, 600])
        actions = QHBoxLayout()
        for label, callback in (
            ("Thêm", self.new_command),
            ("Nhân bản", self.duplicate_command),
            ("Xóa", self.remove_command),
            ("Nhập…", self._import_dialog),
            ("Xuất…", self._export_dialog),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Lệnh tùy chỉnh được ưu tiên trước lệnh có sẵn. Dùng {noidung} trong cách gọi và hành động. "
            "Chính tả luôn chèn lời nói. Nhấn Lưu ở Cài đặt để dùng thay đổi."
        )
        hint.setWordWrap(True)
        hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(hint)
        layout.addLayout(actions)
        layout.addWidget(split, 1)
        self._refresh()
        self._load(None)

    def _changed(self, *_):
        if not self._loading:
            self._dirty = True

    def _refresh(self, selected: str | None = None):
        self._list.blockSignals(True)
        self._list.clear()
        for item in self._commands:
            self._list.addItem(("● " if item["enabled"] else "○ ") + item["name"])
        row = next((i for i, item in enumerate(self._commands) if item["id"] == selected), -1)
        self._list.setCurrentRow(row)
        self._list.blockSignals(False)

    def _load(self, item):
        self._loading = True
        self._editing_id = item["id"] if item else None
        self._name.setText(item["name"] if item else "")
        self._enabled.setChecked(item["enabled"] if item else True)
        self._patterns.setPlainText("\n".join(item["patterns"]) if item else "")
        self._action.setCurrentIndex(self._action.findData(item["intent"] if item else "BROWSER_SEARCH"))
        self._context.setCurrentIndex(self._context.findData(item["context"] if item else "any"))
        self._process.setText(item["process"] if item else "")
        for key, edit in self._params.items():
            value = item["params"].get(key, "") if item else ""
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(value)
            else:
                edit.setText(value)
        self._action_changed()
        self._loading = False
        self._dirty = False
        self._error.clear()

    def _select(self, row):
        target = self._commands[row]["id"] if 0 <= row < len(self._commands) else None
        if self._dirty and not self.save_command():
            self._refresh(self._editing_id)
            return
        item = next((item for item in self._commands if item["id"] == target), None)
        self._load(item)
        self._refresh(target)

    def _action_changed(self, *_):
        intent = self._action.currentData()
        fields = ACTIONS[intent][1]
        for key, edit in self._params.items():
            edit.setVisible(key in fields)
            self._param_labels[key].setVisible(key in fields)
        self._browse.setVisible(intent == "WIN_OPEN_APP")
        self._changed()

    def new_command(self):
        if self._dirty and not self.save_command():
            return
        self._refresh()
        self._load(None)
        self._name.setFocus()

    def _collect(self):
        return dict(
            id=self._editing_id or uuid.uuid4().hex,
            name=self._name.text().strip(),
            enabled=self._enabled.isChecked(),
            patterns=self._patterns.toPlainText().splitlines(),
            intent=self._action.currentData(),
            params={
                key: (
                    self._params[key].toPlainText()
                    if isinstance(self._params[key], QPlainTextEdit)
                    else self._params[key].text()
                )
                for key in ACTIONS[self._action.currentData()][1]
            },
            context=self._context.currentData(),
            process=self._process.text().strip(),
        )

    def save_command(self) -> bool:
        try:
            item = validate_commands([self._collect()])[0]
            revised = [item if old["id"] == item["id"] else old for old in self._commands]
            if not any(old["id"] == item["id"] for old in self._commands):
                revised.append(item)
            self._commands = validate_commands(revised)
        except ValueError as exc:
            self._error.setText(str(exc))
            return False
        self._editing_id = item["id"]
        self._dirty = False
        self._refresh(item["id"])
        self._error.clear()
        return True

    def commands(self) -> list[dict]:
        if self._dirty and not self.save_command():
            raise ValueError(self._error.text())
        return validate_commands(self._commands)

    def duplicate_command(self):
        if self._dirty and not self.save_command():
            return
        item = next((deepcopy(item) for item in self._commands if item["id"] == self._editing_id), None)
        if item:
            item["id"] = uuid.uuid4().hex
            item["name"] = item["name"][:110] + " (bản sao)"
            item["enabled"] = False
            try:
                self._commands = validate_commands([*self._commands, item])
            except ValueError as exc:
                self._error.setText(str(exc))
                return
            self._load(item)
            self._refresh(item["id"])

    def remove_command(self):
        self._commands = [item for item in self._commands if item["id"] != self._editing_id]
        self._refresh()
        self._load(None)

    def preview(self):
        from voicepilot.intent.parser import RuleParser

        try:
            items = self.commands()
            kind = AppKind(self._try_context.currentData())
            process = self._try_process.text().strip()
            ctx = WindowContext(
                1 if kind is not AppKind.NONE else 0,
                0,
                process,
                "",
                kind,
                "edge" if kind is AppKind.BROWSER else None,
            )
            command = RuleParser(
                self._config.website_aliases if self._config else {},
                self._config.app_aliases if self._config else {},
                custom_commands=items,
            ).parse(self._try_text.text(), ctx, Mode.COMMAND)
            name = next((item["name"] for item in items if item["id"] == command.custom_id), "Lệnh có sẵn")
            if command.is_executable:
                self._preview.setText(
                    name
                    + " → "
                    + ACTIONS[command.intent.value][0]
                    + "\n"
                    + "\n".join(f"{key}: {value}" for key, value in command.params.items())
                )
            else:
                self._preview.setText("Không khớp hoặc có xung đột. " + (command.example or ""))
        except ValueError as exc:
            self._preview.setText(str(exc))

    def export_to(self, path: Path):
        data = {"version": 1, "commands": self.commands()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def import_from(self, path: Path):
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Tệp lệnh quá lớn (tối đa 1 MB).")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("Tệp lệnh phải có version = 1.")
        imported = validate_commands(raw.get("commands"))
        revised = self.commands()
        for item in imported:
            existing = next((old for old in revised if old["id"] == item["id"]), None)
            if existing == item:
                continue
            if existing:
                item["id"] = uuid.uuid4().hex
            revised.append(item)
        revised = validate_commands(revised)
        self._commands = revised
        self._refresh()
        self._load(None)

    def _browse_app(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn ứng dụng", "", "Ứng dụng (*.exe)")
        if path:
            self._params["app"].setText(path)

    def _import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Nhập lệnh tùy chỉnh", "", "Lệnh VoicePilot (*.json)")
        if path:
            try:
                self.import_from(Path(path))
            except (OSError, ValueError) as exc:
                self._error.setText("Không nhập được lệnh: " + str(exc))

    def _export_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Xuất lệnh tùy chỉnh", "voicepilot-commands.json", "Lệnh VoicePilot (*.json)"
        )
        if path:
            try:
                self.export_to(Path(path))
            except (OSError, ValueError) as exc:
                self._error.setText("Không xuất được lệnh: " + str(exc))
