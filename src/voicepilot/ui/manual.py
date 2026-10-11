"""In-app user manual with navigation, search and the user's current shortcuts."""

from __future__ import annotations

import re
from importlib import resources

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QTextBrowser,
    QVBoxLayout,
)

from voicepilot.config import AppConfig
from voicepilot.ui.tray import command_guide_path, pretty_hotkey


class UserManualWindow(QDialog):
    """Reusable, nonmodal manual; never opens an external Markdown editor."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.setWindowTitle("VoicePilot — Hướng dẫn sử dụng")
        self._search = QLineEdit()
        self._search.setPlaceholderText("Tìm trong hướng dẫn…")
        self._sections = QListWidget()
        self._sections.setFixedWidth(230)
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setOpenLinks(False)
        self._browser.setStyleSheet(
            "QTextBrowser { background-color: #111827; color: #f9fafb; padding: 14px; }"
        )
        self._browser.document().setDefaultStyleSheet(
            "table { border-collapse: collapse; } "
            "td, th { padding: 8px; border: 1px solid #475569; } a { color: #93c5fd; }"
        )
        self._sections.currentRowChanged.connect(self._show_section)
        self._search.textChanged.connect(self._filter)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._search)
        row = QHBoxLayout()
        row.addWidget(self._sections)
        row.addWidget(self._browser, 1)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.resize(1040, 720)
        self.set_config(config)

    def set_config(self, config: AppConfig) -> None:
        guide = command_guide_path()
        text = (
            guide.read_text(encoding="utf-8-sig")
            if guide
            else resources.files("voicepilot")
            .joinpath("resources", "manual.vi.md")
            .read_text(encoding="utf-8")
        )
        text = text.replace("Ctrl + Alt + Space", pretty_hotkey(config.hotkey_listen)).replace(
            "Ctrl + Alt + D", pretty_hotkey(config.hotkey_mode)
        )
        text = text.replace("**0,8 giây**", f"**{config.silence_end_ms / 1000:g} giây**")
        parts = re.split(r"(?m)^## (.+)$", text)
        self._pages = [("Bắt đầu", parts[0])] + [
            (parts[i], "## " + parts[i] + parts[i + 1]) for i in range(1, len(parts), 2)
        ]
        self._sections.clear()
        self._sections.addItems([title for title, _ in self._pages])
        self._sections.setCurrentRow(0)
        self._filter(self._search.text())

    def _show_section(self, index: int) -> None:
        if 0 <= index < len(self._pages):
            self._browser.setMarkdown(self._pages[index][1])
            self._browser.verticalScrollBar().setValue(0)

    def _filter(self, query: str) -> None:
        query = query.strip().casefold()
        for index, (title, content) in enumerate(self._pages):
            self._sections.item(index).setHidden(bool(query and query not in (title + content).casefold()))
        visible = [i for i in range(len(self._pages)) if not self._sections.item(i).isHidden()]
        if self._sections.currentRow() not in visible:
            self._sections.setCurrentRow(visible[0] if visible else -1)
        if not visible:
            self._browser.setPlainText("Không tìm thấy mục phù hợp.")
