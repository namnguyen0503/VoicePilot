"""Runs parsed commands against the captured window; implements contracts.Executor.

Every command that acts on a window first checks that the captured window is still in the
foreground, before any keystroke or COM call. Failures come back as ActionResult values
and are never raised. Backends are injectable so tests run without Windows.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from voicepilot.config import AppConfig
from voicepilot.contracts import (
    SYSTEM_INTENTS,
    ActionResult,
    AppKind,
    Command,
    ContextProvider,
    ExecutionError,
    Intent,
    ResultStatus,
    WindowContext,
)
from voicepilot.windows import browser, desktop
from voicepilot.windows import input as input_ops
from voicepilot.windows.powerpoint import PowerPointController
from voicepilot.windows.system_controls import SystemControls

log = logging.getLogger(__name__)

_NO_TARGET_MESSAGE = "Chưa có cửa sổ đích, hãy mở ứng dụng rồi thử lại."
_NO_POWERPOINT_MESSAGE = "Hãy chuyển sang cửa sổ PowerPoint rồi thử lại."
_TARGET_CHANGED_MESSAGE = "Cửa sổ đích đã thay đổi, hãy thử lại."
_GENERIC_ERROR_MESSAGE = "Không thực hiện được lệnh."

Handler = Callable[[Command, WindowContext, threading.Event | None], ActionResult]


def _cancelled(cancel: threading.Event | None) -> ActionResult | None:
    if cancel is not None and cancel.is_set():
        return ActionResult(ResultStatus.NEEDS_RETRY, "Đã hủy lệnh.", "cancelled")
    return None


class KeySender(Protocol):
    """Sends keystrokes to the focused window."""

    def send_hotkey(self, *keys: str) -> None:
        """Press a key chord."""
        ...

    def type_unicode(self, text: str, *, word: bool = False) -> None:
        """Type text at the caret."""
        ...


class _SendInputKeys:
    """Default KeySender backed by SendInput."""

    def send_hotkey(self, *keys: str) -> None:
        input_ops.send_hotkey(*keys)

    def type_unicode(self, text: str, *, word: bool = False) -> None:
        if word:
            input_ops.type_unicode(text, word=True)
        else:
            input_ops.type_unicode(text)


class WindowsExecutor:
    """Dispatches each Intent to PowerPoint, browser, desktop or keyboard backends."""

    def __init__(
        self,
        config: AppConfig,
        context_provider: ContextProvider,
        hide_overlay: Callable[[], AbstractContextManager[Any]] | None = None,
        *,
        powerpoint: PowerPointController | None = None,
        keys: KeySender | None = None,
        system_controls: SystemControls | None = None,
    ) -> None:
        """Create the executor.

        Args:
            config: Application settings. Reserved for Windows-specific options; no option is read yet.
            context_provider: Used to re-check the captured window before acting.
            hide_overlay: Factory for a context manager that hides the overlay while a screenshot is taken.
            powerpoint: PowerPoint backend. Defaults to a PowerPointController that starts lazily.
            keys: Keystroke backend. Defaults to SendInput.
        """
        self._system_controls = system_controls if system_controls is not None else SystemControls()
        self._config = config
        self._context = context_provider
        self._hide_overlay = hide_overlay
        self._powerpoint = (
            powerpoint if powerpoint is not None else PowerPointController(context_provider=context_provider)
        )
        self._keys: KeySender = keys if keys is not None else _SendInputKeys()
        self._handlers: dict[Intent, Handler] = {
            Intent.PPT_SAVE: self._ppt_save,
            Intent.PPT_SAVE_AS: self._ppt_save_as,
            Intent.PPT_NEXT_SLIDE: self._ppt_next_slide,
            Intent.PPT_PREV_SLIDE: self._ppt_prev_slide,
            Intent.PPT_START_SHOW: self._ppt_start_show,
            Intent.PPT_STOP_SHOW: self._ppt_stop_show,
            Intent.BROWSER_OPEN_SITE: self._browser_open_site,
            Intent.BROWSER_SEARCH: self._browser_search,
            Intent.BROWSER_NEXT_TAB: self._browser_tab,
            Intent.BROWSER_PREV_TAB: self._browser_tab,
            Intent.BROWSER_NEW_TAB: self._browser_tab,
            Intent.BROWSER_CLOSE_TAB: self._browser_tab,
            Intent.WIN_OPEN_APP: self._win_open_app,
            Intent.WIN_SCREENSHOT: self._win_screenshot,
            Intent.DICTATE_TEXT: self._dictate_text,
            Intent.WIN_HOTKEY: self._hotkey,
        }
        self._handlers.update(dict.fromkeys(SYSTEM_INTENTS, self._system_action))

    def _system_action(self, command, ctx, cancel) -> ActionResult:
        return self._guarded(ctx, lambda: self._system_controls.execute(command.intent, cancel),
                             strict=False, cancel=cancel)

    def execute(
        self, command: Command, context: WindowContext, *, cancel: threading.Event | None = None
    ) -> ActionResult:
        """Run command against the window described by context. Never raises."""
        cancelled = _cancelled(cancel)
        if cancelled is not None:
            return cancelled
        if not command.is_executable:
            return _not_executable(command)
        try:

            def action() -> ActionResult:
                return self._handlers[command.intent](command, context, cancel)

            if command.custom_id is not None:
                return self._guarded(context, action, strict=False, cancel=cancel)
            return action()
        except Exception as exc:
            log.error("Command %s failed: %s", command.intent.value, type(exc).__name__)
            return ActionResult(ResultStatus.ERROR, _GENERIC_ERROR_MESSAGE, "exception")

    def shutdown(self) -> None:
        """Stop the PowerPoint worker thread."""
        self._powerpoint.shutdown()

    def _guarded(
        self,
        ctx: WindowContext,
        action: Callable[[], ActionResult],
        *,
        strict: bool,
        cancel: threading.Event | None,
    ) -> ActionResult:
        """Run action only if the captured window is still the foreground window.

        A strict command needs a real window. A lenient command, such as a screenshot or
        opening a site, may run without one.
        """
        if ctx.hwnd == 0:
            if strict:
                return ActionResult(ResultStatus.NOT_AVAILABLE, _NO_TARGET_MESSAGE, "no_target")
        elif not self._context.is_still_target(ctx):
            return ActionResult(ResultStatus.NEEDS_RETRY, _TARGET_CHANGED_MESSAGE, "target_changed")
        return _cancelled(cancel) or action()

    def _guarded_ppt(
        self, ctx: WindowContext, action: Callable[[], ActionResult], cancel: threading.Event | None
    ) -> ActionResult:
        """Run a PowerPoint action only when the captured window is PowerPoint and still the target.

        The app-kind check comes first so that PowerPoint COM is never reached from another app,
        since the controller falls back to the active presentation when no title matches.
        """
        if ctx.app_kind is not AppKind.POWERPOINT:
            return ActionResult(ResultStatus.NOT_AVAILABLE, _NO_POWERPOINT_MESSAGE, "no_powerpoint_window")
        return self._guarded(ctx, action, strict=True, cancel=cancel)

    def _ppt_save(self, command: Command, ctx: WindowContext, cancel: threading.Event | None) -> ActionResult:
        return self._guarded_ppt(ctx, lambda: self._powerpoint.save(ctx, cancel=cancel), cancel)

    def _ppt_save_as(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        filename = command.params.get("filename", "")
        return self._guarded_ppt(ctx, lambda: self._powerpoint.save_as(ctx, filename, cancel=cancel), cancel)

    def _ppt_next_slide(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        return self._guarded_ppt(ctx, lambda: self._powerpoint.next_slide(ctx, cancel=cancel), cancel)

    def _ppt_prev_slide(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        return self._guarded_ppt(ctx, lambda: self._powerpoint.previous_slide(ctx, cancel=cancel), cancel)

    def _ppt_start_show(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        return self._guarded_ppt(ctx, lambda: self._powerpoint.start_show(ctx, cancel=cancel), cancel)

    def _ppt_stop_show(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        return self._guarded_ppt(ctx, lambda: self._powerpoint.stop_show(ctx, cancel=cancel), cancel)

    def _browser_open_site(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        url = browser.validate_url(command.params.get("url", ""))
        if url is None:
            return ActionResult(ResultStatus.INVALID_PARAM, "Địa chỉ website không hợp lệ.", "invalid_url")
        label = command.params.get("site", "").strip() or url
        return self._guarded(
            ctx, lambda: self._launch(url, ctx, f"Đang mở {label}."), strict=False, cancel=cancel
        )

    def _browser_search(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        query = command.params.get("query", "").strip()
        if not query:
            return ActionResult(ResultStatus.INVALID_PARAM, "Thiếu nội dung tìm kiếm.", "missing_query")
        url = browser.build_search_url(query)
        return self._guarded(
            ctx, lambda: self._launch(url, ctx, "Đang tìm trên Google."), strict=False, cancel=cancel
        )

    def _browser_tab(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        if ctx.app_kind is not AppKind.BROWSER:
            return ActionResult(ResultStatus.NOT_AVAILABLE, "Chưa có cửa sổ trình duyệt.", "no_browser")
        keys = browser.TAB_HOTKEYS[command.intent]
        message = browser.TAB_MESSAGES[command.intent]

        def send() -> ActionResult:
            self._keys.send_hotkey(*keys)
            return ActionResult(ResultStatus.OK, message)

        return self._guarded(ctx, send, strict=True, cancel=cancel)

    def _win_open_app(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        target = command.params.get("app", "")
        name = command.params.get("app_name", "").strip() or target
        if not target:
            return ActionResult(ResultStatus.INVALID_PARAM, "Thiếu tên ứng dụng.", "missing_app")
        cancelled = _cancelled(cancel)
        if cancelled is not None:
            return cancelled
        try:
            desktop.open_app(target)
        except ExecutionError:
            return ActionResult(ResultStatus.NOT_AVAILABLE, f"Không mở được {name}.", "app_launch_failed")
        return ActionResult(ResultStatus.OK, f"Đang mở {name}.")

    def _win_screenshot(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        def capture() -> ActionResult:
            path = desktop.screenshot(ctx, hide_overlay=self._hide_overlay)
            return ActionResult(ResultStatus.OK, "Đã chụp màn hình.", None, {"path": str(path)})

        return self._guarded(ctx, capture, strict=False, cancel=cancel)

    def _hotkey(self, command: Command, ctx: WindowContext, cancel: threading.Event | None) -> ActionResult:
        from voicepilot.custom_commands import hotkey_keys

        try:
            keys = hotkey_keys(command.params.get("keys", ""))
        except ValueError:
            return ActionResult(ResultStatus.INVALID_PARAM, "Tổ hợp phím không hợp lệ.", "invalid_keys")

        def send() -> ActionResult:
            self._keys.send_hotkey(*keys)
            return ActionResult(ResultStatus.OK, "Đã nhấn phím.")

        return self._guarded(ctx, send, strict=True, cancel=cancel)

    def _dictate_text(
        self, command: Command, ctx: WindowContext, cancel: threading.Event | None
    ) -> ActionResult:
        text = command.params.get("text", "")
        if not text.strip():
            return ActionResult(ResultStatus.INVALID_PARAM, "Không có nội dung để chèn.", "empty_text")
        return self._guarded(ctx, lambda: self._type(text, ctx), strict=True, cancel=cancel)

    def _type(self, text: str, ctx: WindowContext) -> ActionResult:
        try:
            if ctx.process_name.casefold() == "winword.exe":
                self._keys.type_unicode(text, word=True)
            else:
                self._keys.type_unicode(text)
        except ExecutionError:
            return ActionResult(ResultStatus.ERROR, "Không chèn được văn bản.", "send_input_failed")
        return ActionResult(ResultStatus.OK, "Đã chèn văn bản.")

    def _launch(self, url: str, ctx: WindowContext, message: str) -> ActionResult:
        try:
            browser.launch_url(url, ctx)
        except ExecutionError:
            return ActionResult(ResultStatus.ERROR, "Không mở được trình duyệt.", "launch_failed")
        return ActionResult(ResultStatus.OK, message)


def _not_executable(command: Command) -> ActionResult:
    hint = f" Thử nói: {command.example}." if command.example else ""
    if command.intent is Intent.UNKNOWN:
        return ActionResult(ResultStatus.INVALID_PARAM, f"Chưa hiểu lệnh.{hint}", "unknown_command")
    missing = ", ".join(command.missing) or "tham số"
    return ActionResult(ResultStatus.INVALID_PARAM, f"Thiếu {missing}.{hint}", "missing_param")
