"""Settings dialog: hotkeys, STT mode, TTS toggle, silence timeout and Gipformer threads."""

from __future__ import annotations

import logging
import sys
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from voicepilot.config import AppConfig, save_config
from voicepilot.contracts import SttBackendMode
from voicepilot.ui.builtin_commands import BuiltinCommandsWidget
from voicepilot.ui.custom_commands import CustomCommandsWidget

log = logging.getLogger(__name__)

STT_CHOICES: tuple[tuple[str, SttBackendMode], ...] = (
    ("Hybrid (Google, dự phòng Gipformer)", SttBackendMode.HYBRID),
    ("Google", SttBackendMode.GOOGLE),
    ("Offline (Gipformer)", SttBackendMode.OFFLINE),
)


def validate_hotkey(spec: str) -> None:
    """Raise ValueError when spec is not a usable hotkey.

    The check uses the Windows hotkey parser when it can be imported. Elsewhere only
    the empty string is rejected, so the dialog still works in development.
    """
    if not spec:
        raise ValueError("Chưa nhập phím tắt.")
    try:
        from voicepilot.windows.hotkeys import parse_hotkey
    except ImportError:
        return
    try:
        parse_hotkey(spec)
    except ValueError as exc:
        raise ValueError(f"Phím tắt không hợp lệ: {spec}") from exc


def show_settings_dialog(dialog: QDialog) -> None:
    """Restore settings even when Windows STARTUPINFO hid Qt's first show.

    With SW_HIDE startup flags, Qt can consider a dialog visible while its native
    HWND remains hidden. A repeated QWidget.show() then does nothing. This guard
    applies only to the explicitly requested settings window, never the overlay.
    """
    if dialog.isMinimized():
        dialog.showNormal()
    else:
        dialog.show()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        hwnd = int(dialog.winId())
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 5)  # SW_SHOW preserves the window's current size/state.
    dialog.raise_()
    dialog.activateWindow()


class SettingsDialog(QDialog):
    """Edit the user-facing settings. Save writes config.toml and emits settings_changed."""

    settings_changed = Signal(object)  # AppConfig
    factory_reset_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        """Build the form from config. Values not shown here are kept as they are."""
        super().__init__(parent)
        self.setWindowTitle("Cài đặt VoicePilot")
        self._config = config

        self._listen_edit = QLineEdit(config.hotkey_listen)
        self._listen_edit.setPlaceholderText("ví dụ: ctrl+alt+space")
        self._mode_edit = QLineEdit(config.hotkey_mode)
        self._mode_edit.setPlaceholderText("ví dụ: ctrl+alt+d")

        self._stt_combo = QComboBox()
        for label, value in STT_CHOICES:
            self._stt_combo.addItem(label, value)
        self._stt_combo.setCurrentIndex(max(0, self._stt_combo.findData(config.stt_mode)))

        self._input_policy_combo = QComboBox()
        for label, value in (
            ("Mặc định về Voice", "voice"),
            ("Nhớ tab đã chọn lần cuối", "remember"),
            ("Luôn mở Text", "text"),
        ):
            self._input_policy_combo.addItem(label, value)
        self._input_policy_combo.setCurrentIndex(
            max(0, self._input_policy_combo.findData(config.input_policy))
        )

        self._tts_check = QCheckBox("Đọc phản hồi bằng giọng nói")
        self._tts_check.setChecked(config.tts_enabled)

        self._vad_check = QCheckBox("Tự dừng khi ngừng nói")
        self._vad_check.setChecked(config.vad_enabled)
        self._vad_check.setToolTip("Tắt: nhấn phím nghe lần nữa để kết thúc, hoặc chờ hết thời lượng.")

        self._silence_spin = QSpinBox()
        self._silence_spin.setRange(300, 3000)
        self._silence_spin.setSingleStep(100)
        self._silence_spin.setSuffix(" ms")
        self._silence_spin.setValue(config.silence_end_ms)
        self._silence_spin.setEnabled(config.vad_enabled)
        self._vad_check.toggled.connect(self._silence_spin.setEnabled)

        self._threads_spin = QSpinBox()
        self._threads_spin.setRange(1, 8)
        self._threads_spin.setValue(config.gipformer_threads)

        form = QFormLayout()
        form.addRow("Phím bắt đầu nghe", self._listen_edit)
        form.addRow("Phím đổi chế độ", self._mode_edit)
        form.addRow("Tab khi mở lại", self._input_policy_combo)
        form.addRow("Nhận dạng giọng nói", self._stt_combo)
        form.addRow("", self._tts_check)
        form.addRow("", self._vad_check)
        form.addRow("Khoảng im lặng để dừng", self._silence_spin)
        form.addRow("Số luồng Gipformer", self._threads_spin)

        self._error = QLabel("")
        self._error.setObjectName("settingsError")
        self._error.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        general = QWidget()
        general.setObjectName("settingsGeneral")
        general.setLayout(form)
        self._custom = CustomCommandsWidget(config.custom_commands, config=config)
        self._builtin = BuiltinCommandsWidget(config.builtin_patterns)
        tabs = QTabWidget()
        tabs.addTab(general, "Chung")
        tabs.addTab(self._builtin, "Prompt mặc định")
        tabs.addTab(self._custom, "Lệnh tùy chỉnh")
        restore = QWidget()
        restore.setObjectName("settingsRestore")
        restore_layout = QVBoxLayout(restore)
        for kind, title, description in (
            ("prompts", "Khôi phục bộ prompt mặc định",
             "Hoàn tác cách gọi của các lệnh có sẵn. Giữ nguyên custom và cài đặt khác."),
            ("settings", "Khôi phục toàn bộ setting",
             "Đưa mọi cài đặt và prompt có sẵn về mặc định. Giữ nguyên mọi lệnh custom."),
            ("factory", "Factory reset",
             "Xóa cấu hình, mọi lệnh custom, log, bản thu debug, lịch sử và cache của app. "
             "App khởi động lại với cài đặt gốc. Giữ bản cài đặt, model và ảnh chụp của bạn."),
        ):
            button = QPushButton(title)
            button.setObjectName("restore_" + kind)
            caption = QLabel(description)
            caption.setWordWrap(True)
            if kind == "factory":
                button.clicked.connect(self._confirm_factory_reset)
            else:
                button.clicked.connect(lambda _checked=False, k=kind: self.restore_draft(k))
            restore_layout.addWidget(button)
            restore_layout.addWidget(caption)
        restore_layout.addStretch()
        tabs.addTab(restore, "Khôi phục")
        layout.addWidget(tabs)
        self.resize(860, 680)
        layout.addWidget(self._error)
        layout.addWidget(buttons)

    def collect(self) -> AppConfig:
        """Return the config described by the form. Raises ValueError on invalid input."""
        listen = self._listen_edit.text().strip().lower()
        mode_key = self._mode_edit.text().strip().lower()
        validate_hotkey(listen)
        validate_hotkey(mode_key)
        if listen == mode_key:
            raise ValueError("Hai phím tắt phải khác nhau.")
        stt_mode = self._stt_combo.currentData()
        if not isinstance(stt_mode, SttBackendMode):
            raise ValueError("Chọn chế độ nhận dạng.")
        return replace(
            self._config,
            hotkey_listen=listen,
            hotkey_mode=mode_key,
            stt_mode=stt_mode,
            input_policy=self._input_policy_combo.currentData(),
            tts_enabled=self._tts_check.isChecked(),
            vad_enabled=self._vad_check.isChecked(),
            silence_end_ms=self._silence_spin.value(),
            gipformer_threads=self._threads_spin.value(),
            custom_commands=self._custom.commands(),
            builtin_patterns=self._builtin.overrides(),
        )

    def restore_draft(self, kind: str):
        """Stage reversible resets until Save, preserving custom editor contents."""
        from voicepilot.restore import restored_config

        if kind == "prompts":
            self._builtin.reset_all()
        elif kind == "settings":
            try:
                custom = self._custom.commands()
            except ValueError as exc:
                self._error.setText(str(exc))
                return
            self._config = restored_config(replace(self._config, custom_commands=custom), kind)
            config = self._config
            self._listen_edit.setText(config.hotkey_listen)
            self._mode_edit.setText(config.hotkey_mode)
            self._stt_combo.setCurrentIndex(self._stt_combo.findData(config.stt_mode))
            self._input_policy_combo.setCurrentIndex(self._input_policy_combo.findData(config.input_policy))
            self._tts_check.setChecked(config.tts_enabled)
            self._vad_check.setChecked(config.vad_enabled)
            self._silence_spin.setValue(config.silence_end_ms)
            self._threads_spin.setValue(config.gipformer_threads)
            self._custom._config = config
            self._builtin.reset_all()
        else:
            raise ValueError("Unknown draft reset")
        self._error.setText("Đã khôi phục bản nháp. Nhấn Lưu để áp dụng, Hủy để giữ cấu hình cũ.")

    def _confirm_factory_reset(self):
        answer = QMessageBox.warning(
            self, "Factory reset VoicePilot",
            "Xóa toàn bộ cài đặt, prompt tùy chỉnh, log, bản thu debug và cache của VoicePilot? "
            "Thao tác không thể hoàn tác. App sẽ khởi động lại với cài đặt gốc.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.factory_reset_requested.emit()

    def accept(self) -> None:
        """Validate, save to config.toml, emit settings_changed and close."""
        try:
            new_config = self.collect()
        except ValueError as exc:
            self._error.setText(str(exc))
            return
        try:
            save_config(new_config)
        except OSError as exc:
            log.error("could not save settings: %s", type(exc).__name__)
            self._error.setText("Không lưu được cài đặt.")
            return
        self._config = new_config
        self.settings_changed.emit(new_config)
        super().accept()
