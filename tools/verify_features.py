"""Repeatable feature verification, before and after a product change.

The default suite exercises the parser and executor with observable fakes. --windows
adds native checks in disposable documents/windows. Each native group is isolated in
a child process with a timeout. A blocked prerequisite is never reported as a pass.
Reports contain only the harness's synthetic text, never the user's debug log.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import platform
import struct
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from voicepilot.config import load_config
from voicepilot.contracts import ActionResult, AppKind, Intent, Mode, ResultStatus, WindowContext
from voicepilot.intent.parser import RuleParser
from voicepilot.windows import browser, desktop
from voicepilot.windows.context import Win32ContextProvider
from voicepilot.windows.executor import WindowsExecutor

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    (Intent.PPT_SAVE, "lưu lại", AppKind.POWERPOINT, {}),
    (Intent.PPT_SAVE_AS, "lưu thành báo cáo kiểm thử", AppKind.POWERPOINT,
     {"filename": "báo cáo kiểm thử"}),
    (Intent.PPT_NEXT_SLIDE, "slide tiếp theo", AppKind.POWERPOINT, {}),
    (Intent.PPT_PREV_SLIDE, "slide trước", AppKind.POWERPOINT, {}),
    (Intent.PPT_START_SHOW, "bắt đầu trình chiếu", AppKind.POWERPOINT, {}),
    (Intent.PPT_STOP_SHOW, "dừng trình chiếu", AppKind.POWERPOINT, {}),
    (Intent.BROWSER_OPEN_SITE, "mở youtube", AppKind.BROWSER,
     {"url": "https://www.youtube.com", "site": "youtube"}),
    (Intent.BROWSER_SEARCH, "tìm đường đi Việt Nam", AppKind.BROWSER,
     {"query": "đường đi Việt Nam"}),
    (Intent.BROWSER_NEXT_TAB, "tab tiếp theo", AppKind.BROWSER, {}),
    (Intent.BROWSER_PREV_TAB, "tab trước", AppKind.BROWSER, {}),
    (Intent.BROWSER_NEW_TAB, "tab mới", AppKind.BROWSER, {}),
    (Intent.BROWSER_CLOSE_TAB, "đóng tab", AppKind.BROWSER, {}),
    (Intent.WIN_OPEN_APP, "mở notepad", AppKind.OTHER,
     {"app": "notepad.exe", "app_name": "notepad"}),
    (Intent.WIN_SCREENSHOT, "chụp màn hình", AppKind.OTHER, {}),
    (Intent.DICTATE_TEXT, "Xin chào Việt Nam 👋", AppKind.OTHER,
     {"text": "Xin chào Việt Nam 👋"}),
)
PPT_METHODS = {
    Intent.PPT_SAVE: "save", Intent.PPT_SAVE_AS: "save_as",
    Intent.PPT_NEXT_SLIDE: "next_slide", Intent.PPT_PREV_SLIDE: "previous_slide",
    Intent.PPT_START_SHOW: "start_show", Intent.PPT_STOP_SHOW: "stop_show",
}


class Blocked(RuntimeError):
    """A missing machine prerequisite prevents observation."""


@dataclass
class Check:
    name: str
    layer: str
    status: str
    elapsed_ms: float
    detail: str = ""


class Runner:
    """Continue independent checks and retain failures as evidence."""

    def __init__(self, layer: str) -> None:
        self.layer = layer
        self.checks: list[Check] = []

    def check(self, name: str, fn: Callable[[], object]) -> None:
        start = time.perf_counter()
        status, detail = "pass", ""
        try:
            value = fn()
            detail = str(value) if value is not None else "Observed expected behavior"
        except Blocked as exc:
            status, detail = "blocked", str(exc)
        except Exception as exc:
            status, detail = "fail", f"{type(exc).__name__}: {exc}"
        self.checks.append(Check(name, self.layer, status, (time.perf_counter() - start) * 1000, detail))


def require(condition: object, message: str) -> None:
    """Assertions remain active even when Python runs with -O."""
    if not condition:
        raise AssertionError(message)


def wait_for(fn: Callable[[], object], timeout: float = 8.0) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Expected observable state did not arrive before timeout")


def contract_suite() -> list[Check]:
    config = load_config(ROOT / "verification-unused-config.toml")
    parser = RuleParser(config.website_aliases, config.app_aliases)
    runner = Runner("fake")
    for intent, text, kind, params in CASES:
        def verify(intent=intent, text=text, kind=kind, params=params):
            ctx = WindowContext(100, 200, "test.exe", "Synthetic target", kind,
                                "edge" if kind is AppKind.BROWSER else None)
            command = parser.parse(text, ctx, Mode.DICTATION if intent is Intent.DICTATE_TEXT
                                   else Mode.COMMAND)
            require(command.intent is intent and command.params == params and command.is_executable,
                    f"Unexpected parse: {command.intent.value} {command.params}")
            provider = Mock(is_still_target=Mock(return_value=True))
            ppt = Mock()
            keys = Mock()
            executor = WindowsExecutor(config, provider, powerpoint=ppt, keys=keys)
            with (patch.object(desktop, "open_app") as launch,
                  patch.object(desktop, "screenshot", return_value=Path("synthetic.png")) as screenshot,
                  patch.object(browser, "launch_url") as url):
                if intent in PPT_METHODS:
                    getattr(ppt, PPT_METHODS[intent]).return_value = ActionResult(ResultStatus.OK, "OK")
                result = executor.execute(command, ctx)
                require(result.status is ResultStatus.OK, f"Execution returned {result.status.value}")
                if intent in PPT_METHODS:
                    expected = (ctx, params["filename"]) if intent is Intent.PPT_SAVE_AS else (ctx,)
                    getattr(ppt, PPT_METHODS[intent]).assert_called_once_with(*expected)
                elif intent in browser.TAB_HOTKEYS:
                    keys.send_hotkey.assert_called_once_with(*browser.TAB_HOTKEYS[intent])
                elif intent is Intent.BROWSER_OPEN_SITE:
                    url.assert_called_once_with(params["url"], ctx)
                elif intent is Intent.BROWSER_SEARCH:
                    url.assert_called_once_with(browser.build_search_url(params["query"]), ctx)
                elif intent is Intent.WIN_OPEN_APP:
                    launch.assert_called_once_with(params["app"])
                elif intent is Intent.WIN_SCREENSHOT:
                    screenshot.assert_called_once_with(ctx, hide_overlay=None)
                    require(result.data["path"] == "synthetic.png", "Screenshot path missing")
                else:
                    keys.type_unicode.assert_called_once_with(params["text"])
        runner.check(intent.value, verify)
    return runner.checks


def native_windows() -> list[tuple[int, int, str, str]]:
    import win32gui
    import win32process

    from voicepilot.windows.context import _image_path

    windows = []
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            windows.append((hwnd, pid, win32gui.GetWindowText(hwnd), _image_path(pid).lower()))
    win32gui.EnumWindows(visit, None)
    return windows


def activate(hwnd: int) -> WindowContext:
    import win32con
    import win32gui
    import win32process

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    user32 = ctypes.WinDLL("user32")
    current = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    foreground = win32gui.GetForegroundWindow()
    thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    attached = bool(thread and thread != current and user32.AttachThreadInput(current, thread, True))
    try:
        if foreground != hwnd:
            win32gui.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current, thread, False)
    wait_for(lambda: win32gui.GetForegroundWindow() == hwnd)
    ctx = Win32ContextProvider().capture()
    require(ctx.hwnd == hwnd, "Foreground context does not match the disposable target")
    return ctx


def make_executor():
    config = load_config(ROOT / "verification-unused-config.toml")
    parser = RuleParser(config.website_aliases, config.app_aliases)
    return parser, WindowsExecutor(config, Win32ContextProvider())


def execute(parser, executor, hwnd: int, text: str, mode: Mode = Mode.COMMAND):
    ctx = activate(hwnd)
    command = parser.parse(text, ctx, mode)
    require(command.is_executable, f"Parser did not understand {text}")
    result = executor.execute(command, ctx)
    require(result.status is ResultStatus.OK, f"{result.status.value}: {result.error_code}")
    return result


def edit_text(hwnd: int) -> str:
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    length = user32.SendMessageW(hwnd, 0x000E, 0, None)  # WM_GETTEXTLENGTH
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, 0x000D, length + 1, ctypes.cast(buffer, ctypes.c_void_p))
    return buffer.value


def keys_suite(folder: Path) -> list[Check]:
    import win32con
    import win32gui
    from PySide6.QtWidgets import QApplication

    from voicepilot.ui.overlay import Overlay
    from voicepilot.windows.start_menu import load_installed_apps, match_app

    app = QApplication.instance() or QApplication([])
    runner = Runner("windows")
    parser, executor = make_executor()
    document = folder / "VoicePilot verification.txt"
    document.write_text("", encoding="utf-8")
    process = subprocess.Popen(["notepad.exe", str(document)])
    hwnd, editor = 0, 0
    overlay = Overlay()
    try:
        hwnd = int(wait_for(lambda: next((w[0] for w in native_windows()
                                         if w[1] == process.pid and "notepad" in w[3]), 0)))
        editor = win32gui.FindWindowEx(hwnd, 0, "Edit", None)
        if not editor:
            raise Blocked("Classic Notepad Edit control is unavailable on this machine")
        def overlay_focus():
            ctx = activate(hwnd)
            overlay.show_bar()
            app.processEvents()
            require(Win32ContextProvider().is_still_target(ctx), "Overlay took foreground focus")
        runner.check("overlay_preserves_focus", overlay_focus)
        def dictate():
            text = "Xin chào Việt Nam 👋 — kiểm thử chính tả."
            execute(parser, executor, hwnd, text, Mode.DICTATION)
            wait_for(lambda: edit_text(editor) == text)
            return "Notepad text equals Vietnamese and emoji input"
        runner.check("DICTATE_TEXT", dictate)
        def changed_focus():
            ctx = activate(hwnd)
            changed = WindowContext(ctx.hwnd + 1, ctx.pid, ctx.process_name, ctx.title, ctx.app_kind, None)
            command = parser.parse("must never be inserted", changed, Mode.DICTATION)
            before = edit_text(editor)
            result = executor.execute(command, changed)
            require(result.status is ResultStatus.NEEDS_RETRY and edit_text(editor) == before,
                    "Changed target was not rejected without typing")
        runner.check("changed_target_rejected", changed_focus)
        def screenshot():
            overlay.show_bar()
            app.processEvents()
            executor._hide_overlay = overlay.hidden_temporarily
            result = execute(parser, executor, hwnd, "chụp màn hình")
            data = Path(result.data["path"]).read_bytes()
            require(data[:8] == b"\x89PNG\r\n\x1a\n", "Screenshot is not a PNG")
            width, height = struct.unpack(">II", data[16:24])
            import mss
            with mss.mss() as grabber:
                require((width, height) in [(m["width"], m["height"]) for m in grabber.monitors[1:]],
                        "Screenshot dimensions do not match a physical monitor")
            return f"PNG {width}x{height}; overlay restored={overlay.isVisible()}"
        runner.check("WIN_SCREENSHOT", screenshot)
        def launch():
            before = {w[0] for w in native_windows() if "notepad" in w[3]}
            execute(parser, executor, hwnd, "mở notepad")
            opened = int(wait_for(lambda: next((w[0] for w in native_windows()
                                               if "notepad" in w[3] and w[0] not in before), 0)))
            win32gui.PostMessage(opened, win32con.WM_CLOSE, 0, 0)
            return "A new Notepad window appeared"
        runner.check("WIN_OPEN_APP", launch)
        def indexed_launch():
            target = match_app("notepad", load_installed_apps())
            require(target, "Installed Notepad was not indexed")
            before = {w[0] for w in native_windows() if "notepad" in w[3]}
            desktop.open_app(target)
            opened = int(wait_for(lambda: next((w[0] for w in native_windows()
                                               if "notepad" in w[3] and w[0] not in before), 0)))
            win32gui.PostMessage(opened, win32con.WM_CLOSE, 0, 0)
            return f"Indexed launch type: {'AppsFolder' if target.startswith('shell:') else 'exe'}"
        runner.check("installed_app_launch", indexed_launch)
    finally:
        overlay.close()
        executor.shutdown()
        if editor:
            win32gui.SendMessage(editor, 0x00B9, 0, 0)  # EM_SETMODIFY: avoid a save prompt
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
    return runner.checks


def ppt_suite(folder: Path) -> list[Check]:
    import pythoncom
    import win32com.client

    runner = Runner("windows")
    parser, executor = make_executor()
    pythoncom.CoInitialize()
    app, deck = None, None
    try:
        try:
            app = win32com.client.DispatchEx("PowerPoint.Application")
        except Exception as exc:
            raise Blocked(f"PowerPoint desktop COM unavailable: {type(exc).__name__}") from exc
        app.Visible = True
        deck = app.Presentations.Add()
        for index in range(1, 4):
            deck.Slides.Add(index, 12)
        deck.SaveAs(str(folder / "VoicePilot verification.pptx"), 24)
        deck.Windows.Item(1).View.GotoSlide(1)
        hwnd = int(wait_for(lambda: next((w[0] for w in native_windows()
                                         if "powerpnt.exe" in w[3] and str(deck.Name) in w[2]), 0)))
        activate(hwnd)
        time.sleep(1)  # Office registers its running object after it loses focus.
        def save_as():
            result = execute(parser, executor, hwnd, "lưu thành báo cáo kiểm thử")
            path = Path(result.data["path"])
            require(path.is_file() and path.name == "báo cáo kiểm thử.pptx", "Unicode SaveAs failed")
        runner.check("PPT_SAVE_AS", save_as)
        def save():
            deck.Slides.Item(1).Shapes.AddTextbox(1, 10, 10, 200, 50).TextFrame.TextRange.Text = "Synthetic"
            execute(parser, executor, hwnd, "lưu lại")
            require(bool(deck.Saved), "COM reported success but the deck is still modified")
        runner.check("PPT_SAVE", save)
        def next_slide():
            execute(parser, executor, hwnd, "slide tiếp theo")
            require(int(deck.Windows.Item(1).View.Slide.SlideIndex) == 2, "Edit view did not reach slide 2")
        runner.check("PPT_NEXT_SLIDE", next_slide)
        def previous():
            execute(parser, executor, hwnd, "slide trước")
            require(int(deck.Windows.Item(1).View.Slide.SlideIndex) == 1, "Edit view did not reach slide 1")
        runner.check("PPT_PREV_SLIDE", previous)
        def start():
            execute(parser, executor, hwnd, "bắt đầu trình chiếu")
            require(int(deck.SlideShowWindow.View.CurrentShowPosition) == 1, "Show did not start at slide 1")
        runner.check("PPT_START_SHOW", start)
        def show_steps():
            raw_hwnd = deck.SlideShowWindow.HWND
            show_hwnd = int(raw_hwnd() if callable(raw_hwnd) else raw_hwnd)
            execute(parser, executor, show_hwnd, "slide tiếp theo")
            require(int(deck.SlideShowWindow.View.CurrentShowPosition) == 2, "Show next failed")
            execute(parser, executor, show_hwnd, "slide trước")
            require(int(deck.SlideShowWindow.View.CurrentShowPosition) == 1, "Show previous failed")
        runner.check("presentation_navigation", show_steps)
        def stop():
            raw_hwnd = deck.SlideShowWindow.HWND
            show_hwnd = int(raw_hwnd() if callable(raw_hwnd) else raw_hwnd)
            execute(parser, executor, show_hwnd, "dừng trình chiếu")
            require(int(app.SlideShowWindows.Count) == 0, "Show window still exists")
        runner.check("PPT_STOP_SHOW", stop)
    finally:
        executor.shutdown()
        if deck is not None:
            with contextlib.suppress(Exception):
                deck.Saved = True
                deck.Close()
        if app is not None:
            with contextlib.suppress(Exception):
                app.Quit()
        pythoncom.CoUninitialize()
    return runner.checks


def browser_suite(folder: Path, name: str) -> list[Check]:
    import http.server
    import threading
    import urllib.request
    import winreg

    import win32con
    import win32gui

    runner = Runner(f"windows-{name}")
    executable = browser.BROWSER_EXECUTABLES[name]
    path = None
    registry_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        with contextlib.suppress(OSError):
            with winreg.OpenKey(root, registry_path) as key:
                path = winreg.QueryValue(key, None)
    if not path or not Path(path).is_file():
        raise Blocked(f"{name} executable is not installed in App Paths")
    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = f"<title>VoicePilot {self.path}</title><textarea id='text'></textarea>".encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_port}"
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([path, f"--user-data-dir={folder / name}", "--no-first-run",
                                "--no-default-browser-check", f"--remote-debugging-port={port}",
                                address + "/first", address + "/second"])
    hwnd = 0
    parser, executor = make_executor()
    def tabs():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
            return [tab for tab in json.load(response) if tab.get("type") == "page"]
    try:
        hwnd = int(wait_for(lambda: next((w[0] for w in native_windows()
                                         if w[1] == process.pid and executable in w[3]), 0), 15))
        wait_for(lambda: len(tabs()) == 2)
        def previous():
            execute(parser, executor, hwnd, "tab trước")
            wait_for(lambda: "/first" in win32gui.GetWindowText(hwnd))
        runner.check("BROWSER_PREV_TAB", previous)
        def next_tab():
            execute(parser, executor, hwnd, "tab tiếp theo")
            wait_for(lambda: "/second" in win32gui.GetWindowText(hwnd))
        runner.check("BROWSER_NEXT_TAB", next_tab)
        def new():
            before = len(tabs())
            execute(parser, executor, hwnd, "tab mới")
            wait_for(lambda: len(tabs()) == before + 1)
        runner.check("BROWSER_NEW_TAB", new)
        def close():
            before = len(tabs())
            execute(parser, executor, hwnd, "đóng tab")
            wait_for(lambda: len(tabs()) == before - 1)
        runner.check("BROWSER_CLOSE_TAB", close)
        # Keep launch_url real, but route its executable into this disposable profile.
        # This verifies ShellExecute and tab creation without touching a personal profile.
        real_shell = browser._shell_open
        def isolated_shell(file, parameters):
            return real_shell(path, f'--user-data-dir="{folder / name}" "{parameters}"')
        with patch.object(browser, "_shell_open", side_effect=isolated_shell):
            def open_site():
                execute(parser, executor, hwnd, "mở " + address + "/opened")
                wait_for(lambda: any(t["url"] == address + "/opened" for t in tabs()))
            runner.check("BROWSER_OPEN_SITE", open_site)
            def search():
                execute(parser, executor, hwnd, "tìm VoicePilot verification")
                wait_for(lambda: any("google.com/search?q=VoicePilot+verification" in t["url"]
                                    for t in tabs()))
            runner.check("BROWSER_SEARCH", search)
    finally:
        executor.shutdown()
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
        server.shutdown()
        server.server_close()
    return runner.checks


def run_probe(name: str, folder: Path) -> list[Check]:
    runner = Runner("windows")
    try:
        if name == "runtime":
            return runtime_suite(folder)
        if name == "keys":
            return keys_suite(folder)
        if name == "ppt":
            return ppt_suite(folder)
        return browser_suite(folder, name)
    except Blocked as exc:
        runner.checks.append(Check(name, "windows", "blocked", 0, str(exc)))
    except Exception as exc:
        runner.checks.append(Check(name, "windows", "fail", 0, f"{type(exc).__name__}: {exc}"))
    return runner.checks


def runtime_suite(folder: Path) -> list[Check]:
    """Native ASR and TTS share one process; synthetic speech is smoke evidence only."""
    import numpy as np

    from voicepilot.audio.capture import convert_to_16k
    from voicepilot.contracts import AudioClip
    from voicepilot.paths import models_dir
    from voicepilot.stt.gipformer import GipformerStt
    from voicepilot.stt.hotwords import command_phrases
    from voicepilot.tts.piper import _load_sherpa_voice, _to_pcm

    os.environ["VOICEPILOT_CONFIG_DIR"] = str(folder / "config")
    config = load_config()
    runner = Runner("runtime")
    asr = GipformerStt(models_dir() / "asr" / "gipformer1.5-int8",
                       hotwords=command_phrases(config.app_aliases, config.website_aliases))
    runner.check("gipformer_model_load", asr.warmup)
    voice = _load_sherpa_voice(models_dir() / "tts" / "vits-piper-vi_VN-vais1000-medium"
                              / "vi_VN-vais1000-medium.onnx")
    def roundtrip():
        audio = voice.generate("Chụp màn hình.", sid=0, speed=1.0)
        require(len(audio.samples) > 0 and audio.sample_rate > 0, "TTS did not generate PCM")
        samples = np.frombuffer(_to_pcm(audio.samples), dtype="<i2")
        normalized = convert_to_16k(samples, audio.sample_rate)
        result = asr.transcribe(AudioClip(normalized.tobytes(), 1))
        parser = RuleParser(config.website_aliases, config.app_aliases)
        require(parser.parse(result.text, None, Mode.COMMAND).intent is Intent.WIN_SCREENSHOT,
                "Synthesized command did not reach the expected intent")
        return f"Synthetic TTS -> ASR -> parser; ASR {result.latency_ms:.0f} ms"
    runner.check("asr_tts_same_process", roundtrip)
    def dictation():
        audio = voice.generate("Xin chào, tôi đang kiểm thử chức năng đọc chính tả.", sid=0, speed=1.0)
        samples = convert_to_16k(np.frombuffer(_to_pcm(audio.samples), dtype="<i2"), audio.sample_rate)
        result = asr.without_hotwords().transcribe(AudioClip(samples.tobytes(), 2))
        require(result.text.strip(), "Unbiased dictation returned no text")
        return "Unbiased decoder returned text from synthetic PCM"
    runner.check("dictation_unbiased_runtime", dictation)
    return runner.checks


def desktop_block_reason(context: WindowContext) -> str | None:
    """A locked or inaccessible desktop cannot supply native feature evidence."""
    if not context.hwnd or context.process_name.lower() in {"lockapp.exe", "logonui.exe", "winlogon.exe"}:
        return "Windows desktop is locked or unavailable; unlock it before native verification"
    return None


def blocked_native_checks(reason: str) -> list[Check]:
    checks = [Check(intent.value, "windows", "blocked", 0, reason) for intent, *_ in CASES]
    checks.extend(Check(name, "windows", "blocked", 0, reason) for name in
                  ("overlay_preserves_focus", "changed_target_rejected", "installed_app_launch"))
    return checks


def write_report(folder: Path, checks: list[Check]) -> None:
    metadata = {"timestamp": datetime.now(UTC).isoformat(), "python": sys.version,
                "platform": platform.platform(), "root": str(ROOT)}
    payload = {**metadata, "checks": [asdict(check) for check in checks]}
    (folder / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# VoicePilot verification", "", f"UTC: {metadata['timestamp']}", "",
             "Fake checks validate contracts. Windows checks observe native results.",
             "Blocked checks need a prerequisite and do not count as passed.", "",
             "| Check | Layer | Status | Time (ms) | Evidence |", "|---|---|---|---:|---|"]
    for check in checks:
        detail = check.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check.name} | {check.layer} | {check.status} | {check.elapsed_ms:.0f} | {detail} |")
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", action="store_true", help="Exercise disposable native app windows")
    parser.add_argument("--checks", action="store_true", help="Also run pytest, ruff and selftest")
    parser.add_argument("--runtime", action="store_true", help="Check native ASR/TTS with synthetic speech")
    parser.add_argument("--output", type=Path, help="New report directory; defaults to local app data")
    parser.add_argument("--probe", choices=("keys", "ppt", "edge", "chrome", "runtime"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VoicePilot" / "verification"
    folder = args.output or base / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=True)
    if args.probe:
        checks = run_probe(args.probe, folder)
        write_report(folder, checks)
        return int(any(check.status == "fail" for check in checks))
    checks = contract_suite()
    if args.checks:
        commands = {
            "pytest": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       "--basetemp", str(folder / "pytest")],
            "ruff": [sys.executable, "-m", "ruff", "check", "src", "tests", "tools"],
            "selftest": [sys.executable, "-m", "voicepilot", "--selftest"],
        }
        runner = Runner("tool")
        for name, command in commands.items():
            def invoke(name=name, command=command):
                environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                               "VOICEPILOT_CONFIG_DIR": str(folder / "config")}
                result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=120)
                (folder / f"{name}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
                require(result.returncode == 0, f"Exit {result.returncode}; see {name}.txt")
                return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "Exit 0"
            runner.check(name, invoke)
        checks.extend(runner.checks)
    if args.runtime:
        target = folder / "runtime"
        result = subprocess.run([sys.executable, __file__, "--probe", "runtime", "--output", str(target)],
                                cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        (folder / "runtime.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        if (target / "report.json").is_file():
            checks.extend(Check(**raw) for raw in json.loads(
                (target / "report.json").read_text(encoding="utf-8"))["checks"])
        else:
            checks.append(Check("runtime", "runtime", "fail", 0,
                                f"Probe exited {result.returncode}; see runtime.txt"))
    if args.windows:
        require(sys.platform == "win32", "--windows requires an interactive Windows desktop")
        reason = desktop_block_reason(Win32ContextProvider().capture())
        if reason:
            checks.extend(blocked_native_checks(reason))
            write_report(folder, checks)
            print(f"blocked windows: {reason}\nReport: {folder / 'report.md'}")
            return 1
        for name in ("keys", "ppt", "edge", "chrome"):
            target = folder / name
            environment = {**os.environ, "QT_QPA_PLATFORM": "windows"}
            try:
                result = subprocess.run([sys.executable, __file__, "--probe", name, "--output", str(target)],
                                        env=environment, cwd=ROOT, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=90)
                (folder / f"{name}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
                if (target / "report.json").is_file():
                    checks.extend(Check(**raw) for raw in json.loads(
                        (target / "report.json").read_text(encoding="utf-8"))["checks"])
                else:
                    checks.append(Check(name, "windows", "fail", 0,
                                        f"Probe exited {result.returncode}; see {name}.txt"))
            except subprocess.TimeoutExpired:
                checks.append(Check(name, "windows", "fail", 90000, "Probe process timed out"))
            write_report(folder, checks)
    write_report(folder, checks)
    for check in checks:
        print(f"{check.status:7} {check.layer:14} {check.name}: {check.detail}")
    print(f"Report: {folder / 'report.md'}")
    return int(any(check.status != "pass" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
