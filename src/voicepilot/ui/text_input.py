"""Explicitly activated text composer; the voice overlay always remains nonactivating."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QTabBar, QToolButton, QVBoxLayout

from voicepilot.contracts import Mode, ResultStatus
from voicepilot.ui.overlay import CHIP_TEXT, _GuiCaller
from voicepilot.ui.settings import show_settings_dialog


class TextInputWindow(QDialog):
    """Keyboard input opened only after the target window has been captured."""

    submitted = Signal(str)
    input_requested = Signal(str)
    mode_requested = Signal()
    cancel_requested = Signal()
    closed = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("VoicePilot — Text")
        self.setObjectName("textInputWindow")
        self._gui = _GuiCaller(self)
        self._busy = False
        self._tabs = QTabBar()
        self._tabs.addTab("Voice")
        self._tabs.addTab("Text")
        self._tabs.setCurrentIndex(1)
        self._tabs.currentChanged.connect(lambda i: self.input_requested.emit("text" if i else "voice"))
        self._mode = QToolButton()
        self._mode.setText(CHIP_TEXT[Mode.COMMAND])
        self._mode.setToolTip("Đổi Ra lệnh / Chính tả")
        self._mode.clicked.connect(self.mode_requested)
        self._target = QLabel("")
        self._cancel = QToolButton()
        self._cancel.setText("✕")
        self._cancel.setToolTip("Hủy lượt")
        self._cancel.clicked.connect(self.cancel_requested)
        self._cancel.hide()
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Nhập lệnh, rồi nhấn Enter…")
        self._edit.setMaxLength(8192)
        self._send = QToolButton()
        self._send.setText("➤")
        self._send.setToolTip("Gửi (Enter)")
        self._send.setAccessibleName("Gửi lệnh")
        self._edit.returnPressed.connect(self._submit)
        self._send.clicked.connect(self._submit)
        self._edit.textChanged.connect(self._update_send)
        self._status = QLabel("Micro đã tắt trong tab Text")
        self._status.setWordWrap(True)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        for widget in (self._tabs, self._mode, self._target, self._cancel):
            top.addWidget(widget)
        top.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self._edit, 1)
        row.addWidget(self._send)
        layout.addLayout(top)
        layout.addLayout(row)
        layout.addWidget(self._status)
        self.resize(680, 160)
        self._update_send()

    def open_for_input(self, target: str) -> None:
        """Activate the composer after the application saved the external target."""
        self._target.setText(target)
        self._tabs.blockSignals(True)
        self._tabs.setCurrentIndex(1)
        self._tabs.blockSignals(False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        show_settings_dialog(self)
        available = self.screen().availableGeometry()
        frame = self.frameGeometry()
        self.move(
            max(available.left(), min(frame.left(), available.right() - frame.width() + 1)),
            max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)),
        )
        self._edit.setFocus()

    def set_mode(self, mode: Mode) -> None:
        self._mode.setText(CHIP_TEXT[mode])
        self._edit.setPlaceholderText(
            "Nhập văn bản để chèn…" if mode is Mode.DICTATION else "Nhập lệnh, rồi nhấn Enter…"
        )

    def _update_send(self) -> None:
        self._send.setEnabled(not self._busy and bool(self._edit.text().strip()))

    def _submit(self) -> None:
        if self._busy or not self._edit.text().strip():
            return
        self._busy = True
        self._edit.setEnabled(False)
        self._update_send()
        self.submitted.emit(self._edit.text())

    def on_state(self, state: str, message: str) -> None:
        self._busy = state in {"LISTENING", "RECOGNIZING", "EXECUTING"}
        self._edit.setEnabled(not self._busy)
        self._cancel.setVisible(self._busy)
        self._update_send()
        if message:
            self._status.setText(message)

    def on_result(self, result) -> None:
        if result.status is ResultStatus.OK:
            self._edit.clear()

    def reject(self) -> None:
        self.closed.emit()
        super().reject()

    def hide_for_capture(self) -> bool:
        visible = self.isVisible()
        self.hide()
        return visible

    def restore_after_capture(self, visible: bool) -> None:
        if visible:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.show()
