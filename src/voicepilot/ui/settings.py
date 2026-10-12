"""Settings dialog: hotkeys, STT mode, TTS toggle, silence timeout, Gipformer threads and a search box."""

from __future__ import annotations

import logging
import sys
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from voicepilot.config import AppConfig, save_config
from voicepilot.contracts import SttBackendMode
from voicepilot.custom_commands import ACTIONS
from voicepilot.ui.builtin_commands import STEP_GROUPS, BuiltinCommandsWidget
from voicepilot.ui.custom_commands import CustomCommandsWidget
from voicepilot.ui.settings_search import SearchItem, rank

log = logging.getLogger(__name__)

TAB_GENERAL = "Chung"
TAB_BUILTIN = "Lệnh có sẵn"
TAB_CUSTOM = "Lệnh tùy chỉnh"
TAB_RESTORE = "Khôi phục"

STT_CHOICES: tuple[tuple[str, SttBackendMode], ...] = (
    ("Hybrid (Google, dự phòng Gipformer)", SttBackendMode.HYBRID),
    ("Google", SttBackendMode.GOOGLE),
    ("Offline (Gipformer)", SttBackendMode.OFFLINE),
)

RESTORE_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("prompts", "Khôi phục cách gọi lệnh có sẵn",
     "Hoàn tác cách gọi của các lệnh có sẵn. Giữ nguyên custom và cài đặt khác."),
    ("settings", "Khôi phục toàn bộ setting",
     "Đưa mọi cài đặt và cách gọi lệnh có sẵn về mặc định. Giữ nguyên mọi lệnh custom."),
    ("factory", "Factory reset",
     "Xóa cấu hình, mọi lệnh custom, log, bản thu debug, lịch sử và cache của app. "
     "App khởi động lại với cài đặt gốc. Giữ bản cài đặt, model và ảnh chụp của bạn."),
)

STEP_TITLES = {"volume": "Mức chỉnh âm lượng mặc định", "brightness": "Mức chỉnh độ sáng mặc định"}
STEP_SEARCH_KEYWORDS = {
    "volume": ("mức mặc định phần trăm %", "âm lượng volume"),
    "brightness": ("mức mặc định phần trăm %", "độ sáng màn hình"),
}
STEP_ROW = {"volume": "WIN_VOLUME_UP", "brightness": "WIN_BRIGHTNESS_UP"}


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


class _SearchEdit(QLineEdit):
    """Search box: Esc clears a non-empty query, Enter opens the best result, Down enters the results."""

    enter_pressed = Signal()
    down_pressed = Signal()

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if key == Qt.Key.Key_Escape and self.text():
            self.clear()
            event.accept()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.enter_pressed.emit()
            event.accept()
        elif key == Qt.Key.Key_Down:
            self.down_pressed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class _ResultList(QListWidget):
    """Result rows: Enter opens the row, Up on the first row returns to the search box, Esc clears."""

    enter_pressed = Signal()
    back_to_search = Signal()
    escaped = Signal()

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.enter_pressed.emit()
            event.accept()
        elif key == Qt.Key.Key_Up and self.currentRow() <= 0:
            self.back_to_search.emit()
            event.accept()
        elif key == Qt.Key.Key_Escape:
            self.escaped.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


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
        self._search = _SearchEdit()
        self._search.setObjectName("settingsSearch")
        self._search.setPlaceholderText("Tìm cài đặt…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        self._search.enter_pressed.connect(self._open_selected_result)
        self._search.down_pressed.connect(self._focus_results)
        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.activated.connect(self._focus_search)
        layout.addWidget(self._search)

        general = QWidget()
        general.setObjectName("settingsGeneral")
        self._general_page = general
        general.setLayout(form)
        self._general_targets = {
            "listen": self._listen_edit,
            "mode": self._mode_edit,
            "input_policy": self._input_policy_combo,
            "stt": self._stt_combo,
            "tts": self._tts_check,
            "vad": self._vad_check,
            "silence": self._silence_spin,
            "threads": self._threads_spin,
        }
        self._custom = CustomCommandsWidget(config.custom_commands, config=config)
        self._builtin = BuiltinCommandsWidget(config.builtin_patterns, steps={
            "volume": config.volume_step_percent,
            "brightness": config.brightness_step_percent,
        })
        self._tabs = QTabWidget()
        self._tabs.addTab(general, TAB_GENERAL)
        self._tabs.addTab(self._builtin, TAB_BUILTIN)
        self._tabs.addTab(self._custom, TAB_CUSTOM)
        restore = QWidget()
        restore.setObjectName("settingsRestore")
        self._restore_page = restore
        restore_layout = QVBoxLayout(restore)
        self._restore_buttons: dict[str, QPushButton] = {}
        for kind, title, description in RESTORE_ACTIONS:
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
            self._restore_buttons[kind] = button
        restore_layout.addStretch()
        self._tabs.addTab(restore, TAB_RESTORE)

        self._results = _ResultList()
        self._results.setObjectName("settingsResults")
        self._results.itemClicked.connect(self._open_item)
        self._results.itemActivated.connect(self._open_item)
        self._results.enter_pressed.connect(self._open_selected_result)
        self._results.back_to_search.connect(self._search.setFocus)
        self._results.escaped.connect(self._search.clear)
        self._no_results = QLabel("Không tìm thấy cài đặt phù hợp.")
        self._no_results.setObjectName("settingsSearchEmpty")
        self._no_results.setVisible(False)
        results_page = QWidget()
        results_page.setObjectName("settingsSearchPage")
        results_layout = QVBoxLayout(results_page)
        results_layout.addWidget(self._results, 1)
        results_layout.addWidget(self._no_results)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._tabs)
        self._stack.addWidget(results_page)
        self._stack.setCurrentWidget(self._tabs)
        self._build_static_search_items()
        layout.addWidget(self._stack)
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
        overrides = self._builtin.overrides()
        steps = self._builtin.steps()
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
            builtin_patterns=overrides,
            volume_step_percent=steps["volume"],
            brightness_step_percent=steps["brightness"],
        )

    def restore_draft(self, kind: str):
        """Stage reversible resets until Save, preserving custom editor contents."""
        from voicepilot.restore import restored_config

        if kind == "prompts":
            self._builtin.reset_phrasings()
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
            self._builtin.set_steps(config.volume_step_percent, config.brightness_step_percent)
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

    # Search

    def _build_static_search_items(self) -> None:
        """Items whose text does not change while the dialog is open."""
        general = (
            ("listen", "Phím bắt đầu nghe", "phím tắt hotkey shortcut nghe bắt đầu"),
            ("mode", "Phím đổi chế độ", "phím tắt hotkey shortcut đổi chế độ"),
            ("input_policy", "Tab khi mở lại", "tab voice text tab mở lại nhớ"),
            ("stt", "Nhận dạng giọng nói", "nhận dạng giọng nói google gipformer offline stt hybrid"),
            ("tts", "Đọc phản hồi bằng giọng nói", "đọc phản hồi giọng nói tts"),
            ("vad", "Tự dừng khi ngừng nói", "tự dừng im lặng vad"),
            ("silence", "Khoảng im lặng để dừng", "khoảng im lặng dừng vad ms"),
            ("threads", "Số luồng Gipformer", "luồng cpu gipformer"),
        )
        self._general_items = [
            SearchItem(f"general:{key}", TAB_GENERAL, title, (keywords,))
            for key, title, keywords in general
        ]
        self._step_items = [
            SearchItem(f"step:{group}", TAB_BUILTIN, STEP_TITLES[group], STEP_SEARCH_KEYWORDS[group],
                       "Dùng khi nói tăng hoặc giảm không kèm số.")
            for group in ("volume", "brightness")
        ]
        self._restore_items = [
            SearchItem(f"restore:{kind}", TAB_RESTORE, title, ("khôi phục reset mặc định",), description)
            for kind, title, description in RESTORE_ACTIONS
        ]

    def _search_index(self) -> list[SearchItem]:
        """Rebuild the dynamic parts (phrasings and custom commands) from the current drafts."""
        phrasings = self._builtin.effective_patterns()
        items = list(self._general_items)
        for name in self._builtin.intents():
            items.append(SearchItem(
                f"builtin:{name}", TAB_BUILTIN, ACTIONS.get(name, (name, {}))[0],
                STEP_SEARCH_KEYWORDS.get(STEP_GROUPS.get(name, ""), ()),
                " · ".join(phrasings.get(name, [])),
            ))
        items.extend(self._step_items)
        for command in self._custom._commands:
            action = ACTIONS.get(command["intent"], ("", {}))[0]
            items.append(SearchItem(
                f"custom:{command['id']}", TAB_CUSTOM, command["name"], (action,),
                " · ".join(command["patterns"]),
            ))
        items.extend(self._restore_items)
        return items

    def _on_search_changed(self, text: str) -> None:
        self._results.clear()
        query = text.strip()
        if not query:
            self._stack.setCurrentWidget(self._tabs)
            return
        found = rank(self._search_index(), query)
        for item in found:
            row = QListWidgetItem(f"{item.title} — {item.tab}")
            row.setData(Qt.ItemDataRole.UserRole, item.key)
            self._results.addItem(row)
        self._no_results.setVisible(not found)
        self._results.setVisible(bool(found))
        self._stack.setCurrentIndex(1)
        if found:
            self._results.setCurrentRow(0)

    def _focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    def _focus_results(self) -> None:
        if self._results.count():
            if self._results.currentRow() < 0:
                self._results.setCurrentRow(0)
            self._results.setFocus()

    def _open_item(self, item: QListWidgetItem) -> None:
        self._open_result(item.data(Qt.ItemDataRole.UserRole))

    def _open_selected_result(self) -> None:
        item = self._results.currentItem() or self._results.item(0)
        if item is not None:
            self._open_result(item.data(Qt.ItemDataRole.UserRole))

    def _open_result(self, key: str) -> None:
        """Clear the search, switch to the target tab and focus the row it names."""
        kind, _, ident = key.partition(":")
        self._search.clear()
        if kind == "general":
            self._tabs.setCurrentWidget(self._general_page)
            self._general_targets[ident].setFocus()
        elif kind == "builtin":
            self._tabs.setCurrentWidget(self._builtin)
            self._builtin.select_intent(ident)
            self._builtin.focus_patterns()
        elif kind == "step":
            self._tabs.setCurrentWidget(self._builtin)
            self._builtin.select_intent(STEP_ROW[ident])
            self._builtin.focus_step()
        elif kind == "custom":
            self._tabs.setCurrentWidget(self._custom)
            if self._custom.select_command(ident):
                self._custom._list.setFocus()
        elif kind == "restore":
            self._tabs.setCurrentWidget(self._restore_page)
            self._restore_buttons[ident].setFocus()
