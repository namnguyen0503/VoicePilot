"""Application bootstrap: parses arguments, wires components and starts the Qt event loop.

Modules that need Windows or heavy optional dependencies (audio, STT, TTS, Windows
automation) are imported inside the functions below, so importing this module never
fails because of them.

Command-line flags:
    --version   print the version and exit 0 (no window, no Qt).
    --selftest  check config, intent rules, model files and imports, then exit 0 or 1.
                No window, no event loop, no hotkeys. The report is printed and written
                to config_dir()/logs/selftest.txt, because a windowed build has no stdout.
    --selftest-runtime  also load ASR/TTS and decode synthetic speech, without microphone or playback.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import logging
import os
import subprocess
import sys
import threading
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import replace
from importlib import import_module, metadata, resources
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QStyle, QSystemTrayIcon

from voicepilot import debuglog
from voicepilot.config import AppConfig, config_dir, load_config, opening_input_channel, save_config
from voicepilot.contracts import (
    ActionResult,
    AppKind,
    Command,
    ContextProvider,
    Executor,
    Intent,
    Mode,
    ResultStatus,
    SttBackendMode,
    WindowContext,
)
from voicepilot.controller import Controller, Recorder, Transcriber, target_label
from voicepilot.ui.manual import UserManualWindow
from voicepilot.ui.overlay import Overlay
from voicepilot.ui.settings import SettingsDialog, show_settings_dialog
from voicepilot.ui.text_input import TextInputWindow
from voicepilot.ui.tray import Tray

if TYPE_CHECKING:
    from voicepilot.stt.gipformer import GipformerStt

log = logging.getLogger("voicepilot")

APP_NAME = "VoicePilot"
DISTRIBUTION_NAME = "voicepilot"
UNKNOWN_VERSION = "0.0.0+dev"
LOG_FILE_NAME = "voicepilot.log"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3
SELFTEST_REPORT_NAME = "selftest.txt"
MANIFEST_RESOURCE = "models.toml"
GIPFORMER_MODEL_DIR = ("asr", "gipformer1.5-int8")
PIPER_MODEL_FILE = ("tts", "vits-piper-vi_VN-vais1000-medium", "vi_VN-vais1000-medium.onnx")
SELFTEST_IMPORTS = ("sherpa_onnx", "sounddevice", "webrtcvad", "speech_recognition", "mss")
SELFTEST_WINDOWS_IMPORTS = ("win32com.client", "pythoncom", "winrt.windows.media.control")

_SELFTEST_PPT = WindowContext(
    hwnd=0,
    pid=0,
    process_name="POWERPNT.EXE",
    title="selftest.pptx",
    app_kind=AppKind.POWERPOINT,
    browser=None,
)
# Sample sentences the rule parser must turn into executable commands.
_SELFTEST_SENTENCES: tuple[tuple[str, WindowContext | None, Intent], ...] = (
    ("slide tiếp theo", _SELFTEST_PPT, Intent.PPT_NEXT_SLIDE),
    ("slide cuối cùng", _SELFTEST_PPT, Intent.PPT_LAST_SLIDE),
    ("tìm kiếm thời tiết Hà Nội", None, Intent.BROWSER_SEARCH),
    ("giá vàng hôm nay bao nhiêu", None, Intent.BROWSER_SEARCH),
    ("mở youtube", None, Intent.BROWSER_OPEN_SITE),
    ("bật tiết kiệm pin", None, Intent.WIN_POWER_SAVER),
    ("tắt tiết kiệm pin", None, Intent.WIN_POWER_RESTORE),
    ("giảm độ sáng", None, Intent.WIN_BRIGHTNESS_DOWN),
    ("tăng độ sáng", None, Intent.WIN_BRIGHTNESS_UP),
    ("giảm âm lượng", None, Intent.WIN_VOLUME_DOWN),
    ("tăng âm lượng", None, Intent.WIN_VOLUME_UP),
    ("tắt tiếng", None, Intent.WIN_MUTE),
    ("play", None, Intent.MEDIA_PLAY),
    ("pause", None, Intent.MEDIA_PAUSE),
    ("next", None, Intent.MEDIA_NEXT),
    ("previous", None, Intent.MEDIA_PREVIOUS),
    ("tạo tài liệu Word mới", None, Intent.WORD_NEW_DOCUMENT),
    ("tạo bảng tính mới", None, Intent.EXCEL_NEW_WORKBOOK),
    ("tạo bài trình chiếu mới", None, Intent.PPT_NEW_PRESENTATION),
    ("mở cửa sổ mới", None, Intent.BROWSER_NEW_WINDOW),
    ("mở cửa sổ ẩn danh", None, Intent.BROWSER_PRIVATE_WINDOW),
    ("đóng cửa sổ", None, Intent.WIN_CLOSE_WINDOW),
    ("tạo tab mới", None, Intent.BROWSER_NEW_TAB),
)

Check = tuple[bool, str]


class _LevelRelay(QObject):
    """Carries microphone levels from the audio thread to the GUI thread."""

    value = Signal(float)

    def push(self, level: float) -> None:
        """Callable handed to the recorder. Safe to call from any thread."""
        self.value.emit(float(level))


class _HotkeyRelay(QObject):
    """Carries global hotkey presses from the hotkey thread to the GUI thread."""

    listen = Signal()
    mode = Signal()


class _DevContextProvider:
    """Non-Windows stand-in: there is never a target window."""

    def capture(self) -> WindowContext:
        return WindowContext(hwnd=0, pid=0, process_name="", title="", app_kind=AppKind.NONE, browser=None)

    def is_still_target(self, ctx: WindowContext) -> bool:
        return True


class _DevExecutor:
    """Non-Windows stand-in: logs the intent and reports that actions need Windows."""

    def execute(
        self, command: Command, context: WindowContext, *, cancel: threading.Event | None = None
    ) -> ActionResult:
        if cancel is not None and cancel.is_set():
            return ActionResult(ResultStatus.NEEDS_RETRY, "Đã hủy lệnh.", "cancelled")
        log.info("dev executor: intent=%s not run on %s", command.intent.value, sys.platform)
        return ActionResult(ResultStatus.NOT_AVAILABLE, "Chỉ thực hiện được trên Windows", "not_windows")


def setup_logging(debug: bool = False) -> None:
    """Write INFO and above to config_dir()/logs/voicepilot.log, rotated at 1 MB with 3 backups.

    With debug, the level drops to DEBUG and the speech-bearing diagnostic log in
    voicepilot.debuglog is enabled as well (logs/debug.log and logs/clips).
    """
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler: logging.Handler
    log_dir = config_dir() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.addHandler(handler)
    if debug:
        try:
            debuglog.enable(log_dir)
            log.warning("debug logging on: transcripts and audio are saved to %s", log_dir)
        except OSError as exc:
            log.warning("debug log unavailable: %s", type(exc).__name__)


def _single_instance_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"VoicePilot-{user}"


def _acquire_single_instance(name: str) -> QLocalServer | None:
    """Return the listening server, or None when another VoicePilot already owns the name."""
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(300):
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(name)  # stale socket left by a crashed run
    server = QLocalServer()

    def discard_connections() -> None:
        while server.hasPendingConnections():
            server.nextPendingConnection().disconnectFromServer()

    server.newConnection.connect(discard_connections)
    if not server.listen(name):
        log.warning("single-instance server unavailable: %s", server.errorString())
    return server


def _load_style() -> str:
    try:
        return resources.files("voicepilot").joinpath("resources", "style.qss").read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("could not load style sheet: %s", type(exc).__name__)
        return ""


def _app_icon(app: QApplication) -> QIcon:
    icon = QIcon(str(resources.files("voicepilot").joinpath("resources", "icon.svg")))
    if icon.isNull():
        icon = app.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
    return icon


def _warm_up(model: Any) -> None:
    try:
        model.warmup()
    except Exception as exc:
        log.warning("speech model warm-up failed: %s", type(exc).__name__)


def _audio_components(
    config: AppConfig,
    on_level: Callable[[float], None],
    gipformer: GipformerStt | None = None,
) -> tuple[Recorder, Transcriber, GipformerStt]:
    """Build the recorder and the hybrid transcriber for config.

    Args:
        config: Settings the components are built from.
        on_level: Receives microphone levels from the recorder.
        gipformer: A Gipformer backend to reuse, so its model stays loaded. None builds a new one.

    The transcriber owns its executors and the controller closes it when it is replaced.
    Gipformer is not owned by the transcriber and is never closed here. Warm-up runs in the
    background whenever offline recognition may be used. It is idempotent, so a reused,
    already-loaded backend returns at once.
    """
    from voicepilot.audio.capture import Recorder as AudioRecorder
    from voicepilot.paths import models_dir
    from voicepilot.stt.gipformer import GipformerStt
    from voicepilot.stt.google import GoogleStt
    from voicepilot.stt.hybrid import HybridTranscriber

    if gipformer is None:
        try:
            from voicepilot.stt.hotwords import command_phrases

            hotwords = command_phrases(config.app_aliases, config.website_aliases,
                                       config.custom_commands, config.builtin_patterns)
        except Exception as exc:
            log.warning("hotword biasing disabled: %s", type(exc).__name__)
            hotwords = []
        model_dir = models_dir().joinpath(*GIPFORMER_MODEL_DIR)
        gipformer = GipformerStt(model_dir, num_threads=config.gipformer_threads, hotwords=hotwords)
    transcriber = HybridTranscriber(
        GoogleStt(),
        gipformer,
        config.stt_mode,
        google_timeout_s=config.google_timeout_s,
    )
    recorder = AudioRecorder(config, on_level=on_level)
    if config.stt_mode is not SttBackendMode.GOOGLE:
        threading.Thread(target=_warm_up, args=(gipformer,), name="voicepilot-warmup", daemon=True).start()
    return recorder, transcriber, gipformer


_INSTALLED_APPS: Any = None  # the one Start Menu index, created on first use and reused


def _installed_apps() -> Any:
    """Return the shared InstalledApps index, creating it and starting its background scan once."""
    global _INSTALLED_APPS
    if _INSTALLED_APPS is None:
        from voicepilot.windows.start_menu import InstalledApps

        apps = InstalledApps()
        apps.start_refresh()
        _INSTALLED_APPS = apps
    return _INSTALLED_APPS


def _make_parser(config: AppConfig) -> Any:
    from voicepilot.intent.parser import RuleParser

    finder = None
    if sys.platform == "win32":
        try:
            finder = _installed_apps().find
        except Exception as exc:
            log.warning("installed app search disabled: %s", type(exc).__name__)
    return RuleParser(
        config.website_aliases, config.app_aliases, app_finder=finder, custom_commands=config.custom_commands,
        builtin_patterns=config.builtin_patterns,
    )


def _make_speaker(config: AppConfig) -> Any:
    from voicepilot.paths import models_dir
    from voicepilot.tts.piper import PiperSpeaker

    # The controller gates speech by the current setting, allowing settings restore
    # to enable TTS even if the app started with it disabled.
    return PiperSpeaker(models_dir().joinpath(*PIPER_MODEL_FILE), enabled=True)


def _windows_components(config: AppConfig, overlay: Overlay) -> tuple[ContextProvider, Executor]:
    from voicepilot.windows.context import Win32ContextProvider
    from voicepilot.windows.executor import WindowsExecutor

    provider = Win32ContextProvider()
    return provider, WindowsExecutor(config, provider, hide_overlay=overlay.hidden_temporarily)


class VoicePilot(QObject):
    """Owns the running components and connects them to the GUI and the hotkeys."""

    def __init__(
        self,
        qt_app: QApplication,
        config: AppConfig,
        *,
        windows: bool,
        icon: QIcon,
        instance_server: QLocalServer,
    ) -> None:
        """Build every component. Nothing is shown or registered until start()."""
        super().__init__(qt_app)
        self._config = config
        self._windows = windows
        self._instance_server = instance_server
        self._hotkeys: Any = None
        self._settings_dialog: SettingsDialog | None = None
        self._closed = False
        self._level_relay = _LevelRelay(self)
        self._hotkey_relay = _HotkeyRelay(self)

        self._manual = None
        self._text_input = TextInputWindow()
        self._input_context = None
        self._overlay = Overlay(config.overlay_pos)
        if windows:
            context_provider, executor = _windows_components(config, self._overlay)
            executor._hide_overlay = self._hidden_input_ui
        else:
            context_provider, executor = _DevContextProvider(), _DevExecutor()
        self._context_provider = context_provider
        self._executor = executor
        recorder, transcriber, self._gipformer = _audio_components(config, self._level_relay.push)
        self._controller = Controller(
            config,
            recorder,
            transcriber,
            _make_parser(config),
            executor,
            _make_speaker(config),
            context_provider,
            parent=self,
        )
        self._tray = Tray(icon, config.hotkey_listen, config.hotkey_mode, parent=self)
        self._wire(qt_app)

    def _wire(self, qt_app: QApplication) -> None:
        controller = self._controller
        overlay = self._overlay
        controller.state_changed.connect(overlay.on_state)
        controller.level_changed.connect(overlay.set_level)
        controller.transcript_changed.connect(overlay.set_transcript)
        controller.target_changed.connect(overlay.set_target)
        controller.mode_changed.connect(lambda mode: overlay.set_mode(mode))
        overlay.cancel_requested.connect(controller.cancel)
        overlay.mode_requested.connect(controller.toggle_mode)
        overlay.input_requested.connect(self._select_input)
        composer = self._text_input
        composer.mode_requested.connect(controller.toggle_mode)
        composer.input_requested.connect(self._select_input)
        composer.submitted.connect(self._submit_text)
        composer.cancel_requested.connect(controller.cancel)
        composer.closed.connect(controller.cancel)
        controller.mode_changed.connect(composer.set_mode)
        controller.state_changed.connect(composer.on_state)
        controller.result_ready.connect(composer.on_result)
        overlay.position_changed.connect(self._on_overlay_moved)

        self._level_relay.value.connect(controller.on_audio_level, Qt.ConnectionType.QueuedConnection)
        self._hotkey_relay.listen.connect(self._listen_requested, Qt.ConnectionType.QueuedConnection)
        self._hotkey_relay.mode.connect(controller.toggle_mode, Qt.ConnectionType.QueuedConnection)

        tray = self._tray
        tray.overlay_toggled.connect(self._show_input_panel)
        tray.settings_requested.connect(self._open_settings)
        tray.commands_requested.connect(self._open_manual)
        tray.quit_requested.connect(qt_app.quit)
        qt_app.aboutToQuit.connect(self.shutdown)

    def start(self) -> None:
        """Show the tray icon, register hotkeys and set the initial overlay mode."""
        self._overlay.set_mode(self._controller.mode)
        self._tray.show()
        self._hotkeys = self._register_hotkeys(self._config)

    def shutdown(self) -> None:
        """Cancel the active turn, close the transcriber, stop hotkeys and release worker threads.

        Safe to call twice. The controller closes the transcriber it holds.
        """
        if self._closed:
            return
        self._closed = True
        self._controller.shutdown()
        self._stop_hotkeys()
        self._overlay.hide()
        self._overlay.deleteLater()
        self._text_input.hide()
        self._text_input.deleteLater()
        if self._manual is not None:
            self._manual.hide()
            self._manual.deleteLater()
        self._tray.shutdown()
        self._tray.deleteLater()
        if self._settings_dialog is not None:
            self._settings_dialog.hide()
            self._settings_dialog.deleteLater()
        self.deleteLater()

    @contextlib.contextmanager
    def _hidden_input_ui(self):
        """Keep both voice and text panels out of screenshots without stealing focus."""
        visible = self._text_input._gui.run(self._text_input.hide_for_capture)
        try:
            with self._overlay.hidden_temporarily():
                yield
        finally:
            self._text_input._gui.run(lambda: self._text_input.restore_after_capture(visible))

    def _listen_requested(self) -> None:
        """Open the chosen input tab, or stop an existing voice capture."""
        if self._closed:
            return
        if self._controller._turn is not None:
            self._controller.toggle_listen()
            return
        if self._text_input.isVisible():
            self._select_input("text")
            return
        self._select_input(opening_input_channel(self._config))
        if self._controller.input_channel == "voice":
            self._controller.toggle_listen()

    def _show_input_panel(self, shown: bool) -> None:
        if shown:
            self._select_input(opening_input_channel(self._config))
            if self._controller.input_channel == "voice":
                self._overlay.set_pinned(True)
        else:
            self._controller.cancel()
            self._text_input.hide()
            self._overlay.set_pinned(False)

    def _select_input(self, channel: str) -> None:
        if self._closed:
            return
        if channel == "text":
            turn = self._controller._turn
            ctx = turn.context if turn is not None else self._context_provider.capture()
            if ctx.hwnd and ctx.hwnd != int(self._text_input.winId()):
                self._input_context = ctx
            elif self._input_context is None:
                self._input_context = ctx
        elif self._text_input.isVisible() and self._windows and self._input_context is not None:
            from voicepilot.windows.context import restore_text_target

            restore_text_target(self._input_context, int(self._text_input.winId()))
        self._controller.set_input_channel(channel)
        self._overlay.set_input_channel(channel)
        if channel == "text":
            self._text_input.move(self._overlay._initial_position())
            self._text_input.open_for_input(target_label(self._input_context))
        else:
            self._text_input.hide()
            self._overlay.show_bar()
        if self._config.last_input_channel != channel:
            self._config = replace(self._config, last_input_channel=channel)
            try:
                save_config(self._config)
            except OSError as exc:
                log.warning("could not save input choice: %s", type(exc).__name__)

    def _submit_text(self, text: str) -> None:
        ctx = self._input_context
        if ctx is None or self._controller.input_channel != "text":
            self._text_input.on_state("IDLE", "Hãy mở lại tab Text.")
            return
        if self._windows:
            from voicepilot.windows.context import restore_text_target

            if not restore_text_target(ctx, int(self._text_input.winId())):
                self._text_input.on_state("IDLE", "Cửa sổ đích đã thay đổi, hãy mở lại tab Text.")
                return
        self._text_input.hide()
        accepted = self._controller.submit_text(text, ctx)
        if not accepted:
            self._text_input.on_state("IDLE", "Đang xử lý lượt trước.")
        # Remain visible for status/retry, without reclaiming the target's keyboard.
        self._text_input.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._text_input.show()

    def _open_manual(self) -> None:
        if self._manual is None:
            self._manual = UserManualWindow(self._config)
        else:
            self._manual.set_config(self._config)
        show_settings_dialog(self._manual)

    # ----- hotkeys ------------------------------------------------------------------

    def _register_hotkeys(self, config: AppConfig) -> Any:
        if not self._windows:
            print("VoicePilot: phím tắt toàn cục chỉ hoạt động trên Windows.", file=sys.stderr)
            return None
        from voicepilot.windows.hotkeys import HotkeyManager

        manager = HotkeyManager()
        bindings = (
            (config.hotkey_listen, lambda: self._hotkey_relay.listen.emit()),
            (config.hotkey_mode, lambda: self._hotkey_relay.mode.emit()),
        )
        for spec, callback in bindings:
            try:
                manager.register(spec, callback)
            except ValueError:
                log.error("invalid hotkey setting ignored")
        manager.start()
        return manager

    def _stop_hotkeys(self) -> None:
        if self._hotkeys is None:
            return
        try:
            self._hotkeys.stop()
        except Exception as exc:
            log.warning("hotkey stop failed: %s", type(exc).__name__)
        self._hotkeys = None

    # ----- settings and overlay position --------------------------------------------

    def _open_settings(self) -> None:
        if self._settings_dialog is None:
            dialog = SettingsDialog(self._config)
            dialog.settings_changed.connect(self._apply_settings)
            dialog.factory_reset_requested.connect(self._factory_reset)
            dialog.finished.connect(lambda _code, d=dialog: self._on_settings_closed(d))
            self._settings_dialog = dialog
        show_settings_dialog(self._settings_dialog)

    def _factory_reset(self) -> None:
        from voicepilot.restore import request_factory_reset

        try:
            request_factory_reset()
        except (OSError, ValueError) as exc:
            if self._settings_dialog is not None:
                self._settings_dialog._error.setText("Không chuẩn bị được factory reset: " + str(exc))
            return
        self._restart_requested = True
        self._controller.cancel()
        QApplication.instance().quit()

    def _on_settings_closed(self, dialog: SettingsDialog) -> None:
        if self._settings_dialog is dialog:
            self._settings_dialog = None
        dialog.deleteLater()

    def _apply_settings(self, config: AppConfig) -> None:
        """Rebuild the recorder and transcriber. Gipformer is rebuilt only when its thread count changes.

        The controller closes the replaced transcriber when it swaps the new one in.
        """
        reuse = (
            self._gipformer
            if (
                config.gipformer_threads == self._config.gipformer_threads
                and config.custom_commands == self._config.custom_commands
                and config.builtin_patterns == self._config.builtin_patterns
                and config.app_aliases == self._config.app_aliases
                and config.website_aliases == self._config.website_aliases
            )
            else None
        )
        position_changed = config.overlay_pos != self._config.overlay_pos
        self._config = config
        if position_changed:
            self._overlay.set_position(config.overlay_pos)
        recorder, transcriber, self._gipformer = _audio_components(config, self._level_relay.push, reuse)
        self._controller.apply_settings(config, recorder, transcriber, parser=_make_parser(config))
        update_executor = getattr(self._executor, "update_config", None)
        if update_executor is not None:
            update_executor(config)
        self._stop_hotkeys()
        self._hotkeys = self._register_hotkeys(config)
        self._tray.set_hotkeys(config.hotkey_listen, config.hotkey_mode)
        if self._manual is not None:
            self._manual.set_config(config)

    def _on_overlay_moved(self, x: int, y: int) -> None:
        self._config = replace(self._config, overlay_pos=(x, y))
        try:
            save_config(self._config)
        except OSError as exc:
            log.warning("could not save overlay position: %s", type(exc).__name__)


# ----- command line ---------------------------------------------------------------


def _app_version() -> str:
    try:
        return metadata.version(DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Vietnamese voice assistant for Windows.")
    parser.add_argument("--wait-for-exit", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="check config, intent rules, model files and imports without a window; exit 0 when all pass",
    )
    parser.add_argument(
        "--selftest-runtime",
        action="store_true",
        help="verify ASR/TTS models together using synthetic speech; no microphone or playback",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log at DEBUG and save transcripts, parsed commands, turn audio to logs/debug.log, logs/clips",
    )
    return parser


def _emit(lines: Sequence[str]) -> None:
    """Print lines to stdout. A windowed build has no stdout, so nothing is printed there."""
    stream = sys.stdout
    if stream is None:
        return
    for line in lines:
        try:
            print(line, file=stream)
        except UnicodeEncodeError:
            print(line.encode("ascii", "backslashreplace").decode("ascii"), file=stream)


def _guarded(label: str, check: Callable[[], list[Check]]) -> list[Check]:
    """Run one group of checks. An unexpected exception becomes a failed check, never a crash."""
    try:
        return check()
    except Exception as exc:
        return [(False, f"{label}: {type(exc).__name__}: {exc}")]


def _check_config() -> list[Check]:
    load_config()  # falls back to defaults on a bad user file, so only a broken install fails here
    return [(True, f"config: loaded, folder {config_dir()}")]


def _check_rules() -> list[Check]:
    from voicepilot.intent.parser import RuleParser

    config = load_config()
    parser = RuleParser(config.website_aliases, config.app_aliases)
    results: list[Check] = []
    for text, context, expected in _SELFTEST_SENTENCES:
        command = parser.parse(text, context, Mode.COMMAND)
        ok = command.is_executable and command.intent is expected
        results.append((ok, f'rules: "{text}" -> {command.intent.value}, executable={command.is_executable}'))
    custom = dict(id="selftest", name="Selftest", enabled=True,
                  patterns=["hỏi google về {topic}"], intent="BROWSER_SEARCH",
                  params={"query": "{topic}"}, context="any", process="")
    custom_parser = RuleParser(config.website_aliases, config.app_aliases, custom_commands=[custom])
    command = custom_parser.parse("hỏi google về Giá vàng", None, Mode.COMMAND)
    results.append((command.custom_id == "selftest" and command.params == {"query": "Giá vàng"},
                    "rules: custom template preserves the captured query"))
    return results


def _check_model_files() -> list[Check]:
    from voicepilot.paths import models_dir

    text = resources.files("voicepilot").joinpath("resources", MANIFEST_RESOURCE).read_text(encoding="utf-8")
    manifest = tomllib.loads(text)
    base = models_dir()
    results: list[Check] = []
    for model in manifest.get("model", []):
        for item in model.get("files", []):
            path = base.joinpath(model["dest"], item["name"])
            ok = path.is_file()
            state = "present" if ok else f"MISSING, expected at {path}"
            results.append((ok, f"model file {model['dest']}/{item['name']}: {state}"))
    if not results:
        results.append((False, "models: manifest lists no files"))
    return results


def _import_targets() -> tuple[str, ...]:
    """Modules the selftest imports. pywin32 is required only on Windows."""
    if sys.platform == "win32":
        return SELFTEST_IMPORTS + SELFTEST_WINDOWS_IMPORTS
    return SELFTEST_IMPORTS


def _check_imports(names: Sequence[str]) -> list[Check]:
    results: list[Check] = []
    for name in names:
        try:
            module = import_module(name)
            if name == "winrt.windows.media.control":
                _check_media_projection(module)
        except Exception as exc:
            results.append((False, f"import {name}: {type(exc).__name__}: {exc}"))
        else:
            results.append((True, f"import {name}"))
    return results


def _check_media_projection(module) -> None:
    """Exercise frozen WinRT projections with a read-only session lookup."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def lookup():
        import winrt.runtime

        from voicepilot.windows.system_controls import init_media_apartment

        async def request():
            async with asyncio.timeout(3):
                manager = await module.GlobalSystemMediaTransportControlsSessionManager.request_async()
                manager.get_current_session()

        init_media_apartment()
        try:
            asyncio.run(request())
        finally:
            winrt.runtime.uninit_apartment()

    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(lookup).result()


def _write_report(lines: Sequence[str]) -> bool:
    try:
        target = config_dir() / "logs" / SELFTEST_REPORT_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _check_speech_runtime() -> list[Check]:
    """Exercise native model inference together without touching audio devices."""
    import numpy as np

    from voicepilot.audio.capture import convert_to_16k
    from voicepilot.audio.neural_vad import MODEL_RELATIVE_PATH, SileroSpeechDetector
    from voicepilot.audio.vad import FRAME_SAMPLES, Decision, Endpointer
    from voicepilot.contracts import AudioClip
    from voicepilot.intent.parser import RuleParser
    from voicepilot.paths import models_dir
    from voicepilot.stt.gipformer import GipformerStt
    from voicepilot.stt.hotwords import command_phrases
    from voicepilot.tts.piper import _load_sherpa_voice, _to_pcm

    config = load_config()
    base = models_dir()
    backend = GipformerStt(
        base.joinpath(*GIPFORMER_MODEL_DIR),
        num_threads=config.gipformer_threads,
        hotwords=command_phrases(config.app_aliases, config.website_aliases),
    )
    backend.warmup()
    voice = _load_sherpa_voice(base.joinpath(*PIPER_MODEL_FILE))
    detector = SileroSpeechDetector(base / MODEL_RELATIVE_PATH)
    checks: list[Check] = [(True, "runtime: ASR and TTS models loaded in one process")]
    parser = RuleParser(config.website_aliases, config.app_aliases)
    for index, (text, dictation) in enumerate(
        (
            ("Chụp màn hình.", False),
            ("Xin chào, tôi đang kiểm thử chức năng đọc chính tả.", True),
        )
    ):
        audio = voice.generate(text, sid=0, speed=1.0)
        if not len(audio.samples) or audio.sample_rate <= 0:
            checks.append((False, "runtime: TTS returned no valid PCM"))
            continue
        samples = convert_to_16k(np.frombuffer(_to_pcm(audio.samples), dtype="<i2"), audio.sample_rate)
        detector.reset()
        endpoint = Endpointer(silence_end_ms=800, max_duration_ms=15000,
                              no_speech_timeout_ms=5000, is_speech=detector,
                              resume_speech_frames=3)
        padded = np.concatenate((samples, np.zeros(32000, dtype=np.int16)))
        decision = Decision.CONTINUE
        for offset in range(0, len(padded) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
            decision = endpoint.feed(padded[offset:offset + FRAME_SAMPLES].astype("<i2").tobytes())
            if decision is not Decision.CONTINUE:
                break
        checks.append((decision is Decision.END_SILENCE,
                       "runtime: neural VAD detected synthetic speech and its endpoint"))
        decoder = backend.without_hotwords() if dictation else backend
        result = decoder.transcribe(AudioClip(samples.tobytes(), index))
        if dictation:
            checks.append((bool(result.text.strip()), "runtime: unbiased dictation returned text"))
        else:
            command = parser.parse(result.text, None, Mode.COMMAND)
            checks.append(
                (
                    command.intent is Intent.WIN_SCREENSHOT,
                    "runtime: synthetic TTS -> ASR -> screenshot intent",
                )
            )
    return checks


def run_selftest(import_names: Sequence[str] | None = None, *, runtime: bool = False) -> int:
    """Run the smoke checks and return the exit code: 0 when all pass and the report is written.

    Args:
        import_names: Modules to import. None uses the modules required on this platform.
        runtime: Also load speech models and infer synthetic audio, without audio devices.

    The report goes to stdout and to config_dir()/logs/selftest.txt. Model inference is opt-in.
    """
    names = _import_targets() if import_names is None else tuple(import_names)
    checks: list[Check] = []
    checks += _guarded("config", _check_config)
    checks += _guarded("rules", _check_rules)
    checks += _guarded("models", _check_model_files)
    checks += _guarded("imports", lambda: _check_imports(names))
    if runtime:
        checks += _guarded("runtime", _check_speech_runtime)

    passed = all(ok for ok, _ in checks)
    lines = [f"{APP_NAME} {_app_version()} selftest"]
    lines += [f"[{'OK' if ok else 'FAIL'}] {text}" for ok, text in checks]
    lines.append("SELFTEST OK" if passed else "SELFTEST FAILED")
    written = _write_report(lines)
    _emit(lines)
    if not written:
        _emit([f"{APP_NAME}: cannot write the selftest report under {config_dir() / 'logs'}"])
    return 0 if passed and written else 1


def _wait_for_previous_process(pid: int) -> None:
    """Relaunch only after the former app releases workers and open Windows files."""
    if sys.platform != "win32":
        raise OSError("Factory restart requires Windows")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise OSError("Cannot wait for the previous app")
    try:
        if kernel.WaitForSingleObject(handle, 60000) != 0:
            raise OSError("Previous app has not shut down; factory reset postponed")
    finally:
        kernel.CloseHandle(handle)


def _restart_command(debug: bool) -> list[str]:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "voicepilot"]
    if debug:
        command.append("--debug")
    return [*command, "--wait-for-exit", str(os.getpid())]


def _restart_application(debug: bool) -> None:
    """Start an independent PyInstaller instance rather than a worker of the old app."""
    subprocess.Popen(
        _restart_command(debug),
        env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def main(argv: list[str] | None = None) -> int:
    """Run VoicePilot, or answer --version or --selftest without a window, and return the exit code.

    Args:
        argv: Command-line arguments without the program name. None reads sys.argv.
    """
    args = _build_arg_parser().parse_args(argv)
    if args.version:
        _emit([f"{APP_NAME} {_app_version()}"])
        return 0
    if args.selftest or args.selftest_runtime:
        return run_selftest(runtime=args.selftest_runtime)

    if args.wait_for_exit is not None:
        try:
            _wait_for_previous_process(args.wait_for_exit)
        except OSError as exc:
            _emit([str(exc)])
            return 1

    existing = QApplication.instance()
    qt_app = existing if isinstance(existing, QApplication) else QApplication([APP_NAME])
    qt_app.setApplicationName(APP_NAME)
    qt_app.setQuitOnLastWindowClosed(False)

    server = _acquire_single_instance(_single_instance_name())
    if server is None:
        log.info("another instance is already running")
        QMessageBox.information(None, APP_NAME, "VoicePilot đang chạy.")
        return 0

    from voicepilot.restore import complete_factory_reset

    try:
        complete_factory_reset()
    except (OSError, ValueError) as exc:
        QMessageBox.critical(None, APP_NAME, "Chưa hoàn tất factory reset: " + str(exc))
        server.close()
        return 1
    setup_logging(debug=args.debug)

    config = load_config()
    qt_app.setStyleSheet(_load_style())
    windows = sys.platform == "win32"
    if not windows:
        log.warning("running outside Windows: global hotkeys and desktop actions are disabled")
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("system tray is not available; the tray menu is not shown")

    app = VoicePilot(
        qt_app,
        config,
        windows=windows,
        icon=_app_icon(qt_app),
        instance_server=server,
    )
    app.start()
    code = qt_app.exec()
    restart = getattr(app, "_restart_requested", False)
    app.shutdown()
    server.close()
    if restart:
        try:
            _restart_application(args.debug)
        except OSError:
            QMessageBox.critical(None, APP_NAME,
                                 "Không mở lại được VoicePilot. Hãy mở app để hoàn tất factory reset.")
            return 1
    return code
