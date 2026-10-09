"""Application bootstrap: parses arguments, wires components and starts the Qt event loop.

Modules that need Windows or heavy optional dependencies (audio, STT, TTS, Windows
automation) are imported inside the functions below, so importing this module never
fails because of them.

Command-line flags:
    --version   print the version and exit 0 (no window, no Qt).
    --selftest  check config, intent rules, model files and imports, then exit 0 or 1.
                No window, no event loop, no hotkeys. The report is printed and written
                to config_dir()/logs/selftest.txt, because a windowed build has no stdout.
"""

from __future__ import annotations

import argparse
import getpass
import logging
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

from voicepilot.config import AppConfig, config_dir, load_config, save_config
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
from voicepilot.controller import Controller, Recorder, Transcriber
from voicepilot.ui.overlay import Overlay
from voicepilot.ui.settings import SettingsDialog
from voicepilot.ui.tray import Tray, show_command_help

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
SELFTEST_WINDOWS_IMPORTS = ("win32com.client", "pythoncom")

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
    ("tìm kiếm thời tiết Hà Nội", None, Intent.BROWSER_SEARCH),
    ("mở youtube", None, Intent.BROWSER_OPEN_SITE),
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

    def execute(self, command: Command, context: WindowContext) -> ActionResult:
        log.info("dev executor: intent=%s not run on %s", command.intent.value, sys.platform)
        return ActionResult(ResultStatus.NOT_AVAILABLE, "Chỉ thực hiện được trên Windows", "not_windows")


def setup_logging() -> None:
    """Write INFO and above to config_dir()/logs/voicepilot.log, rotated at 1 MB with 3 backups."""
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler: logging.Handler
    try:
        log_dir = config_dir() / "logs"
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
    root.setLevel(logging.INFO)
    root.addHandler(handler)


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
        model_dir = models_dir().joinpath(*GIPFORMER_MODEL_DIR)
        gipformer = GipformerStt(model_dir, num_threads=config.gipformer_threads)
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


def _make_parser(config: AppConfig) -> Any:
    from voicepilot.intent.parser import RuleParser

    return RuleParser(config.website_aliases, config.app_aliases)


def _make_speaker(config: AppConfig) -> Any:
    from voicepilot.paths import models_dir
    from voicepilot.tts.piper import PiperSpeaker

    return PiperSpeaker(models_dir().joinpath(*PIPER_MODEL_FILE), enabled=config.tts_enabled)


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
        super().__init__()
        self._config = config
        self._windows = windows
        self._instance_server = instance_server
        self._hotkeys: Any = None
        self._settings_dialog: SettingsDialog | None = None
        self._closed = False
        self._level_relay = _LevelRelay(self)
        self._hotkey_relay = _HotkeyRelay(self)

        self._overlay = Overlay(config.overlay_pos)
        if windows:
            context_provider, executor = _windows_components(config, self._overlay)
        else:
            context_provider, executor = _DevContextProvider(), _DevExecutor()
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
        overlay.position_changed.connect(self._on_overlay_moved)

        self._level_relay.value.connect(controller.on_audio_level, Qt.ConnectionType.QueuedConnection)
        self._hotkey_relay.listen.connect(controller.toggle_listen, Qt.ConnectionType.QueuedConnection)
        self._hotkey_relay.mode.connect(controller.toggle_mode, Qt.ConnectionType.QueuedConnection)

        tray = self._tray
        tray.overlay_toggled.connect(overlay.set_pinned)
        tray.settings_requested.connect(self._open_settings)
        tray.commands_requested.connect(lambda: show_command_help(None))
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
        self._tray.hide()

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
            dialog.finished.connect(lambda _code, d=dialog: self._on_settings_closed(d))
            self._settings_dialog = dialog
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_settings_closed(self, dialog: SettingsDialog) -> None:
        if self._settings_dialog is dialog:
            self._settings_dialog = None
        dialog.deleteLater()

    def _apply_settings(self, config: AppConfig) -> None:
        """Rebuild the recorder and transcriber. Gipformer is rebuilt only when its thread count changes.

        The controller closes the replaced transcriber when it swaps the new one in.
        """
        reuse = self._gipformer if config.gipformer_threads == self._config.gipformer_threads else None
        self._config = config
        recorder, transcriber, self._gipformer = _audio_components(config, self._level_relay.push, reuse)
        self._controller.apply_settings(config, recorder, transcriber)
        self._stop_hotkeys()
        self._hotkeys = self._register_hotkeys(config)
        self._tray.set_hotkeys(config.hotkey_listen, config.hotkey_mode)

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
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="check config, intent rules, model files and imports without a window; exit 0 when all pass",
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
            import_module(name)
        except Exception as exc:
            results.append((False, f"import {name}: {type(exc).__name__}: {exc}"))
        else:
            results.append((True, f"import {name}"))
    return results


def _write_report(lines: Sequence[str]) -> bool:
    try:
        target = config_dir() / "logs" / SELFTEST_REPORT_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def run_selftest(import_names: Sequence[str] | None = None) -> int:
    """Run the smoke checks and return the exit code: 0 when all pass and the report is written.

    Args:
        import_names: Modules to import. None uses the modules required on this platform.

    The report goes to stdout and to config_dir()/logs/selftest.txt. It never loads a speech model.
    """
    names = _import_targets() if import_names is None else tuple(import_names)
    checks: list[Check] = []
    checks += _guarded("config", _check_config)
    checks += _guarded("rules", _check_rules)
    checks += _guarded("models", _check_model_files)
    checks += _guarded("imports", lambda: _check_imports(names))

    passed = all(ok for ok, _ in checks)
    lines = [f"{APP_NAME} {_app_version()} selftest"]
    lines += [f"[{'OK' if ok else 'FAIL'}] {text}" for ok, text in checks]
    lines.append("SELFTEST OK" if passed else "SELFTEST FAILED")
    written = _write_report(lines)
    _emit(lines)
    if not written:
        _emit([f"{APP_NAME}: cannot write the selftest report under {config_dir() / 'logs'}"])
    return 0 if passed and written else 1


def main(argv: list[str] | None = None) -> int:
    """Run VoicePilot, or answer --version or --selftest without a window, and return the exit code.

    Args:
        argv: Command-line arguments without the program name. None reads sys.argv.
    """
    args = _build_arg_parser().parse_args(argv)
    if args.version:
        _emit([f"{APP_NAME} {_app_version()}"])
        return 0
    if args.selftest:
        return run_selftest()

    setup_logging()
    existing = QApplication.instance()
    qt_app = existing if isinstance(existing, QApplication) else QApplication([APP_NAME])
    qt_app.setApplicationName(APP_NAME)
    qt_app.setQuitOnLastWindowClosed(False)

    server = _acquire_single_instance(_single_instance_name())
    if server is None:
        log.info("another instance is already running")
        QMessageBox.information(None, APP_NAME, "VoicePilot đang chạy.")
        return 0

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
    app.shutdown()
    return code
