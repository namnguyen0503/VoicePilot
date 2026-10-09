"""Floating dark overlay bar (about 520 px) that does not take focus."""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

from PySide6.QtCore import QObject, QPoint, QPropertyAnimation, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from voicepilot.contracts import Mode

log = logging.getLogger(__name__)

BAR_WIDTH = 520
BAR_MIN_HEIGHT = 64
EDGE_MARGIN = 16  # gap between the bar and the bottom of the available screen area
AUTO_HIDE_MS = 2500
GUI_CALL_TIMEOUT_S = 5.0
LEVEL_RANGE = 1000
LEVEL_ANIM_MS = 90
READY_TEXT = "Sẵn sàng"
CHIP_TEXT = {Mode.COMMAND: "Ra lệnh", Mode.DICTATION: "Chính tả"}
_ACTIVE_STATES = ("LISTENING", "RECOGNIZING", "EXECUTING")

# Win32 constants used to keep the bar from becoming the foreground window.
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080


class _GuiCaller(QObject):
    """Runs callables on the GUI thread on behalf of worker threads."""

    _call = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._call.connect(self._execute, Qt.ConnectionType.QueuedConnection)

    def run(self, fn: Callable[[], Any]) -> Any:
        """Return fn() evaluated on the GUI thread. Runs inline when already on it.

        Blocks the calling thread until the GUI thread has run fn. Raises TimeoutError
        if the GUI thread does not answer within GUI_CALL_TIMEOUT_S.
        """
        if QThread.currentThread() == self.thread():
            return fn()
        done = threading.Event()
        box: dict[str, Any] = {}

        def job() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # re-raised on the calling thread
                box["error"] = exc
            finally:
                done.set()

        self._call.emit(job)
        if not done.wait(GUI_CALL_TIMEOUT_S):
            raise TimeoutError("GUI thread did not respond")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _execute(self, job: Callable[[], None]) -> None:
        job()


class Overlay(QWidget):
    """Frameless status bar showing mode, target, level, status, transcript and a cancel button.

    The window never takes focus, so the target application keeps the keyboard.
    """

    cancel_requested = Signal()
    position_changed = Signal(int, int)  # saved by the app after a drag ends

    def __init__(
        self,
        position: tuple[int, int] | None = None,
        *,
        auto_hide_ms: int = AUTO_HIDE_MS,
        parent: QWidget | None = None,
    ) -> None:
        """Create the hidden bar. position is the last saved top-left corner, if any."""
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("overlayWindow")
        self._gui = _GuiCaller(self)
        self._position = position
        self._placed = False
        self._pinned = False
        self._turn_active = False
        self._full_transcript = ""
        self._drag_offset: QPoint | None = None

        self._build_ui()
        self.setFixedWidth(BAR_WIDTH)
        self.setMinimumHeight(BAR_MIN_HEIGHT)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(auto_hide_ms)
        self._hide_timer.timeout.connect(self._on_auto_hide)

        self._level_anim = QPropertyAnimation(self._meter, b"value", self)
        self._level_anim.setDuration(LEVEL_ANIM_MS)

    # ----- construction -------------------------------------------------------------

    def _build_ui(self) -> None:
        frame = QFrame(self)
        frame.setObjectName("overlay")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(12, 8, 12, 8)
        inner.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._mode_chip = QLabel(CHIP_TEXT[Mode.COMMAND])
        self._mode_chip.setObjectName("overlayMode")
        self._target = QLabel("")
        self._target.setObjectName("overlayTarget")
        self._status = QLabel("")
        self._status.setObjectName("overlayStatus")
        self._cancel = QToolButton()
        self._cancel.setObjectName("overlayCancel")
        self._cancel.setText("✕")
        self._cancel.setToolTip("Hủy lượt")
        self._cancel.clicked.connect(self.cancel_requested)
        top.addWidget(self._mode_chip)
        top.addWidget(self._target)
        top.addStretch(1)
        top.addWidget(self._status)
        top.addWidget(self._cancel)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._transcript = QLabel("")
        self._transcript.setObjectName("overlayTranscript")
        self._transcript.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._meter = QProgressBar()
        self._meter.setObjectName("overlayLevel")
        self._meter.setRange(0, LEVEL_RANGE)
        self._meter.setValue(0)
        self._meter.setTextVisible(False)
        self._meter.setFixedWidth(90)
        bottom.addWidget(self._transcript, 1)
        bottom.addWidget(self._meter)

        inner.addLayout(top)
        inner.addLayout(bottom)
        self._cancel.setVisible(False)

    # ----- content setters (GUI thread) ---------------------------------------------

    def set_mode(self, mode: Mode) -> None:
        """Show the mode chip for mode."""
        self._mode_chip.setText(CHIP_TEXT[mode])

    def set_target(self, label: str) -> None:
        """Show the name of the target application."""
        self._target.setText(label)

    def set_status(self, text: str) -> None:
        """Show a short status line."""
        self._status.setText(text)

    def set_transcript(self, text: str) -> None:
        """Show the recognized text, elided to fit the bar."""
        self._full_transcript = text
        self._refresh_transcript()

    def set_level(self, level: float) -> None:
        """Animate the level meter towards level (0.0 to 1.0)."""
        target = round(min(1.0, max(0.0, level)) * LEVEL_RANGE)
        self._level_anim.stop()
        self._level_anim.setStartValue(self._meter.value())
        self._level_anim.setEndValue(target)
        self._level_anim.start()

    def set_pinned(self, pinned: bool) -> None:
        """Keep the bar visible when idle (tray option), or let it auto-hide again."""
        self._pinned = pinned
        self._hide_timer.stop()
        if pinned:
            if not self._turn_active:
                self.set_status(READY_TEXT)
            self.show_bar()
        elif not self._turn_active:
            self.hide()

    def show_bar(self) -> None:
        """Show the bar without activating it. The first show places it near the bottom of the screen."""
        if not self._placed:
            self._placed = True
            self.move(self._initial_position())
        self.show()

    # ----- turn state (connected to Controller.state_changed) -----------------------

    def on_state(self, state: str, message: str) -> None:
        """Update the bar for a controller state change."""
        if state in _ACTIVE_STATES:
            self._turn_active = True
            self._cancel.setVisible(True)
            self._hide_timer.stop()
            if state != "LISTENING":
                self.set_level(0.0)
            if message:
                self.set_status(message)
            self.show_bar()
        elif state in ("DONE", "ERROR"):
            self._turn_active = False
            self._cancel.setVisible(False)
            self.set_level(0.0)
            self.set_status(message)
            self.show_bar()
            self._hide_timer.start()
        elif state == "IDLE":
            self._turn_active = False
            self._cancel.setVisible(False)
            self.set_level(0.0)
            if message:  # cancelled: say so briefly, then hide
                self.set_status(message)
                self.show_bar()
                self._hide_timer.start()

    def _on_auto_hide(self) -> None:
        if self._turn_active:
            return
        if self._pinned:
            self.set_status(READY_TEXT)
        else:
            self.hide()

    # ----- screenshot support -------------------------------------------------------

    @contextlib.contextmanager
    def hidden_temporarily(self) -> Iterator[None]:
        """Hide the bar for the duration of the block, then restore it if it was visible.

        Safe to call from any thread: the hide and show steps run on the GUI thread.
        """
        was_visible = self._gui.run(self._hide_now)
        try:
            yield
        finally:
            self._gui.run(lambda: self._restore(was_visible))

    def _hide_now(self) -> bool:
        visible = self.isVisible()
        if visible:
            self.hide()
            QApplication.processEvents()
        return visible

    def _restore(self, was_visible: bool) -> None:
        if was_visible:
            self.show()

    # ----- Qt events ----------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        """Re-apply the no-activate window style each time the bar is shown."""
        super().showEvent(event)
        self._apply_no_activate_style()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        """Re-elide the transcript when the bar width changes."""
        super().resizeEvent(event)
        self._refresh_transcript()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Start dragging with the left mouse button."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Move the bar while dragging."""
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Finish a drag and report the new position so it can be saved."""
        if self._drag_offset is not None:
            self._drag_offset = None
            pos = self.pos()
            self._position = (pos.x(), pos.y())
            self.position_changed.emit(pos.x(), pos.y())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # ----- helpers ------------------------------------------------------------------

    def _refresh_transcript(self) -> None:
        width = max(self._transcript.width(), 1)
        elided = self._transcript.fontMetrics().elidedText(
            self._full_transcript, Qt.TextElideMode.ElideRight, width
        )
        self._transcript.setText(elided)

    def _initial_position(self) -> QPoint:
        """Saved position when it is still on a screen, else centred above the bottom edge."""
        self.adjustSize()
        if self._position is not None:
            saved = QPoint(*self._position)
            screen = QGuiApplication.screenAt(saved)
            if screen is not None:
                return self._clamp(saved, screen.availableGeometry())
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return QPoint(0, 0)
        area = primary.availableGeometry()
        x = area.x() + (area.width() - self.width()) // 2
        y = area.y() + area.height() - self.height() - EDGE_MARGIN
        return QPoint(x, y)

    def _clamp(self, point: QPoint, area: QRect) -> QPoint:
        x = min(max(point.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(point.y(), area.top()), area.bottom() - self.height() + 1)
        return QPoint(x, y)

    def _apply_no_activate_style(self) -> None:
        """On Windows, add WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW so clicks never activate the bar."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            get_style = user32.GetWindowLongPtrW
            get_style.restype = ctypes.c_ssize_t
            get_style.argtypes = [wintypes.HWND, ctypes.c_int]
            set_style = user32.SetWindowLongPtrW
            set_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            hwnd = int(self.winId())
            style = get_style(hwnd, _GWL_EXSTYLE)
            set_style(hwnd, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW)
        except (OSError, AttributeError, ValueError) as exc:
            log.debug("could not set no-activate style: %s", type(exc).__name__)
