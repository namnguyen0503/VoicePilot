"""System tray icon and menu: show overlay, settings, command list, quit."""

from __future__ import annotations

import html
import tomllib
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QMenu,
    QSystemTrayIcon,
    QWidget,
)

_KEY_NAMES = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "space": "Space"}


def pretty_hotkey(spec: str) -> str:
    """Format a hotkey spec such as "ctrl+alt+space" for display as "Ctrl+Alt+Space"."""
    parts = [part.strip() for part in spec.split("+") if part.strip()]
    return "+".join(
        _KEY_NAMES.get(part.lower(), part.upper() if len(part) == 1 else part.capitalize()) for part in parts
    )


class Tray(QObject):
    """Tray icon with the VoicePilot menu. Emits one signal per menu action."""

    overlay_toggled = Signal(bool)
    settings_requested = Signal()
    commands_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        icon: QIcon,
        hotkey_listen: str,
        hotkey_mode: str,
        parent: QObject | None = None,
    ) -> None:
        """Create the tray icon. Call show() to make it visible."""
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        self._show_action = QAction("Hiện thanh", menu)
        self._show_action.setCheckable(True)
        self._show_action.toggled.connect(self.overlay_toggled)
        settings_action = QAction("Cài đặt…", menu)
        settings_action.triggered.connect(self.settings_requested)
        commands_action = QAction("Hướng dẫn sử dụng / Danh sách lệnh", menu)
        commands_action.triggered.connect(self.commands_requested)
        quit_action = QAction("Thoát", menu)
        quit_action.triggered.connect(self.quit_requested)
        menu.addAction(self._show_action)
        menu.addAction(settings_action)
        menu.addAction(commands_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu
        self._tray.setContextMenu(menu)
        self.set_hotkeys(hotkey_listen, hotkey_mode)

    def show(self) -> None:
        """Make the tray icon visible."""
        self._tray.show()

    def hide(self) -> None:
        """Hide the tray icon."""
        self._tray.hide()

    def shutdown(self) -> None:
        """Release the parentless menu while Qt and its Python callbacks are alive."""
        self._tray.hide()
        self._tray.setContextMenu(None)
        self._menu.deleteLater()

    def set_hotkeys(self, hotkey_listen: str, hotkey_mode: str) -> None:
        """Update the tooltip that lists the hotkeys."""
        self._tray.setToolTip(
            f"VoicePilot\nNghe: {pretty_hotkey(hotkey_listen)}\nĐổi chế độ: {pretty_hotkey(hotkey_mode)}"
        )


def command_guide_path() -> Path | None:
    """Path of the user-facing command guide next to the app, or None when it is not shipped."""
    try:
        from voicepilot.paths import app_base_dir
    except ImportError:
        return None
    path = app_base_dir() / "docs" / "COMMANDS.vi.md"
    return path if path.is_file() else None


def show_command_help(parent: QWidget | None = None) -> None:
    """Show the built-in manual window instead of launching an external editor."""
    from voicepilot.config import load_config
    from voicepilot.ui.manual import UserManualWindow

    dialog = UserManualWindow(load_config())
    dialog.exec()


def _commands_html() -> str:
    """Render the intents in commands.toml as a short HTML list: one example per intent."""
    text = resources.files("voicepilot").joinpath("resources", "commands.toml").read_text(encoding="utf-8")
    intents = tomllib.loads(text).get("intent", [])
    rows: list[str] = []
    for entry in intents:
        name = html.escape(str(entry.get("name", "")))
        example = html.escape(str(entry.get("example", "")))
        patterns = ", ".join(html.escape(str(p)) for p in entry.get("patterns", []))
        line = f"<p><b>{name}</b>"
        if example:
            line += f"<br>Thử nói: {example}"
        if patterns:
            line += f"<br><span>Mẫu: {patterns}</span>"
        rows.append(line + "</p>")
    return "<html><body>" + "".join(rows) + "</body></html>"
