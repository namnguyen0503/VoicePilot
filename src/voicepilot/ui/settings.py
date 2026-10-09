"""Settings dialog: hotkeys, STT mode, TTS toggle, silence timeout and Gipformer threads."""

from __future__ import annotations

import logging
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from voicepilot.config import AppConfig, save_config
from voicepilot.contracts import SttBackendMode

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


class SettingsDialog(QDialog):
    """Edit the user-facing settings. Save writes config.toml and emits settings_changed."""

    settings_changed = Signal(object)  # AppConfig

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

        self._tts_check = QCheckBox("Đọc phản hồi bằng giọng nói")
        self._tts_check.setChecked(config.tts_enabled)

        self._silence_spin = QSpinBox()
        self._silence_spin.setRange(300, 3000)
        self._silence_spin.setSingleStep(100)
        self._silence_spin.setSuffix(" ms")
        self._silence_spin.setValue(config.silence_end_ms)

        self._threads_spin = QSpinBox()
        self._threads_spin.setRange(1, 8)
        self._threads_spin.setValue(config.gipformer_threads)

        form = QFormLayout()
        form.addRow("Phím bắt đầu nghe", self._listen_edit)
        form.addRow("Phím đổi chế độ", self._mode_edit)
        form.addRow("Nhận dạng giọng nói", self._stt_combo)
        form.addRow("", self._tts_check)
        form.addRow("Tự kết thúc sau im lặng", self._silence_spin)
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
        layout.addLayout(form)
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
            tts_enabled=self._tts_check.isChecked(),
            silence_end_ms=self._silence_spin.value(),
            gipformer_threads=self._threads_spin.value(),
        )

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
