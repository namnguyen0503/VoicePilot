"""Edit shipped command phrasings without modifying their native actions."""

from copy import deepcopy

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voicepilot.builtin_commands import default_table, validate_overrides
from voicepilot.custom_commands import ACTIONS


class BuiltinCommandsWidget(QWidget):
    """Detached drafts; every row switch and Settings Save validates pending text."""

    def __init__(self, overrides: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("builtinCommands")
        self._rows = [row for row in default_table()["intent"] if row.get("patterns")]
        self._overrides = deepcopy(validate_overrides(overrides))
        self._current = -1
        self._list = QListWidget()
        self._list.setMinimumWidth(260)
        for row in self._rows:
            self._list.addItem(ACTIONS.get(row["name"], (row["example"], {}))[0])
        self._help = QLabel()
        self._help.setWordWrap(True)
        self._patterns = QPlainTextEdit()
        self._patterns.setPlaceholderText("Mỗi dòng một cách gọi")
        self._error = QLabel()
        self._error.setObjectName("settingsError")
        self._error.setWordWrap(True)
        restore = QPushButton("Khôi phục prompt của lệnh này")
        restore.clicked.connect(self._restore_current)
        info = QLabel("Sửa cách gọi của các lệnh có sẵn. Các dòng mới thay thế bộ cách gọi cũ. "
                      "Lệnh custom vẫn được ưu tiên. Nhấn Lưu ở Cài đặt để áp dụng.")
        info.setWordWrap(True)
        right = QVBoxLayout()
        right.addWidget(info)
        right.addWidget(self._help)
        right.addWidget(self._patterns, 1)
        right.addWidget(self._error)
        right.addWidget(restore)
        layout = QHBoxLayout(self)
        layout.addWidget(self._list)
        layout.addLayout(right, 1)
        self._list.currentRowChanged.connect(self._select)
        self._list.setCurrentRow(0)

    def _stash(self):
        if self._current < 0:
            return
        row = self._rows[self._current]
        patterns = self._patterns.toPlainText().splitlines()
        clean = validate_overrides({row["name"]: patterns})[row["name"]]
        if clean == row["patterns"]:
            self._overrides.pop(row["name"], None)
        else:
            self._overrides[row["name"]] = clean

    def _select(self, index):
        try:
            self._stash()
        except ValueError as exc:
            self._error.setText(str(exc))
            self._list.blockSignals(True)
            self._list.setCurrentRow(self._current)
            self._list.blockSignals(False)
            return
        self._current = index
        self._load_current()

    def _load_current(self):
        row = self._rows[self._current]
        self._patterns.setPlainText("\n".join(self._overrides.get(row["name"], row["patterns"])))
        placeholders = sorted({part.split("}")[0] + "}" for pattern in row["patterns"]
                               for part in pattern.split("{")[1:]})
        self._help.setText("Tham số dùng được: " + ", ".join("{" + p for p in placeholders)
                           if placeholders else "Lệnh này không dùng tham số.")
        self._error.clear()

    def select_intent(self, name):
        self._list.setCurrentRow(next(i for i, row in enumerate(self._rows) if row["name"] == name))

    def _restore_current(self):
        self._overrides.pop(self._rows[self._current]["name"], None)
        self._load_current()

    def reset_all(self):
        self._overrides.clear()
        self._load_current()

    def overrides(self) -> dict:
        self._stash()
        return deepcopy(self._overrides)
