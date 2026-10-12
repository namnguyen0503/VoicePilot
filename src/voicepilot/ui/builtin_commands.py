"""Edit shipped command phrasings and per-command options without modifying native actions."""

from copy import deepcopy

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from voicepilot.builtin_commands import default_table, validate_overrides
from voicepilot.custom_commands import ACTIONS

STEP_DEFAULT = 20
# Commands whose option box is shown, mapped to the shared step they use.
STEP_GROUPS = {
    "WIN_VOLUME_UP": "volume",
    "WIN_VOLUME_DOWN": "volume",
    "WIN_BRIGHTNESS_UP": "brightness",
    "WIN_BRIGHTNESS_DOWN": "brightness",
}
STEP_CAPTIONS = {
    "volume": "Dùng chung cho tăng và giảm âm lượng. Nói kèm số để chỉnh khác, ví dụ “tăng âm lượng 30%”.",
    "brightness": "Dùng chung cho tăng và giảm độ sáng. Nói kèm số để chỉnh khác, ví dụ “tăng độ sáng 30%”.",
}


class BuiltinCommandsWidget(QWidget):
    """Detached drafts; every row switch and Settings Save validates pending text."""

    def __init__(self, overrides: dict, parent=None, *, steps: dict | None = None):
        super().__init__(parent)
        self.setObjectName("builtinCommands")
        self._rows = [row for row in default_table()["intent"] if row.get("patterns")]
        self._overrides = deepcopy(validate_overrides(overrides))
        self._steps = {"volume": STEP_DEFAULT, "brightness": STEP_DEFAULT}
        self._steps.update(steps or {})
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
        self._step_spin = QSpinBox()
        self._step_spin.setRange(1, 100)
        self._step_spin.setSuffix(" %")
        self._step_spin.valueChanged.connect(self._step_changed)
        self._step_caption = QLabel()
        self._step_caption.setWordWrap(True)
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Mức mặc định khi không nói số"))
        step_row.addWidget(self._step_spin)
        step_row.addStretch()
        self._step_box = QWidget()
        self._step_box.setObjectName("builtinStepOptions")
        step_layout = QVBoxLayout(self._step_box)
        step_layout.setContentsMargins(0, 0, 0, 0)
        step_layout.addLayout(step_row)
        step_layout.addWidget(self._step_caption)
        self._step_box.hide()
        restore = QPushButton("Khôi phục mặc định của lệnh này")
        restore.clicked.connect(self._restore_current)
        info = QLabel("Sửa cách gọi của các lệnh có sẵn. Các dòng mới thay thế bộ cách gọi cũ. "
                      "Lệnh custom vẫn được ưu tiên. Nhấn Lưu ở Cài đặt để áp dụng.")
        info.setWordWrap(True)
        right = QVBoxLayout()
        right.addWidget(info)
        right.addWidget(self._help)
        right.addWidget(self._step_box)
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

    def _group_of_current(self) -> str | None:
        if 0 <= self._current < len(self._rows):
            return STEP_GROUPS.get(self._rows[self._current]["name"])
        return None

    def _load_current(self):
        row = self._rows[self._current]
        self._patterns.setPlainText("\n".join(self._overrides.get(row["name"], row["patterns"])))
        placeholders = sorted({part.split("}")[0] + "}" for pattern in row["patterns"]
                               for part in pattern.split("{")[1:]})
        self._help.setText("Tham số dùng được: " + ", ".join("{" + p for p in placeholders)
                           if placeholders else "Lệnh này không dùng tham số.")
        group = self._group_of_current()
        self._step_box.setVisible(group is not None)
        if group is not None:
            self._step_caption.setText(STEP_CAPTIONS[group])
            self._step_spin.blockSignals(True)
            self._step_spin.setValue(self._steps[group])
            self._step_spin.blockSignals(False)
        self._error.clear()

    def _step_changed(self, value: int):
        group = self._group_of_current()
        if group is not None:
            self._steps[group] = value

    def select_intent(self, name):
        self._list.setCurrentRow(next(i for i, row in enumerate(self._rows) if row["name"] == name))

    def focus_patterns(self):
        self._patterns.setFocus()

    def focus_step(self):
        """Focus the default-step spin box; it is only visible for volume and brightness rows."""
        if self._group_of_current() is not None:
            self._step_spin.setFocus()

    def intents(self) -> list[str]:
        return [row["name"] for row in self._rows]

    def effective_patterns(self) -> dict[str, list[str]]:
        """Phrasings as they would be saved, without validating or changing the drafts."""
        result = {}
        for index, row in enumerate(self._rows):
            patterns = self._overrides.get(row["name"], row["patterns"])
            if index == self._current:
                lines = [line.strip() for line in self._patterns.toPlainText().splitlines() if line.strip()]
                patterns = lines or patterns
            result[row["name"]] = list(patterns)
        return result

    def _restore_current(self):
        row = self._rows[self._current]
        self._overrides.pop(row["name"], None)
        group = self._group_of_current()
        if group is not None:
            self._steps[group] = STEP_DEFAULT
        self._load_current()

    def reset_phrasings(self):
        """Drop every phrasing edit; option values are left as they are."""
        self._overrides.clear()
        self._load_current()

    def reset_all(self):
        """Drop every phrasing edit and reset both step values to the default."""
        self._overrides.clear()
        self._steps = {"volume": STEP_DEFAULT, "brightness": STEP_DEFAULT}
        self._load_current()

    def steps(self) -> dict:
        return dict(self._steps)

    def set_steps(self, volume: int, brightness: int):
        """Load step values, for example from a restored config."""
        self._steps = {"volume": volume, "brightness": brightness}
        self._load_current()

    def overrides(self) -> dict:
        self._stash()
        return deepcopy(self._overrides)
