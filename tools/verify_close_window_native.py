"""Close only disposable Notepad windows; probe desktop/taskbar without sending keys."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import Mock

from verify_features import Runner, activate, execute, make_executor, native_windows, require, wait_for


def main():
    import win32gui
    import win32process
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete, isValid

    from voicepilot.config import AppConfig
    from voicepilot.contracts import AppKind, Mode, WindowContext
    from voicepilot.windows import input as input_ops
    from voicepilot.windows.executor import WindowsExecutor
    from voicepilot.windows.window_close import _user32, is_closable_window

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder = Path(__file__).resolve().parents[1] / "build" / ("close-window-native-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    os.environ["VOICEPILOT_CONFIG_DIR"] = str(folder / "VoicePilot")
    app = QApplication.instance() or QApplication([])
    runner = Runner("windows_close_window")
    parser, executor = make_executor()
    processes = []

    def blocked_shells():
        user = _user32()
        handles = {int(user.GetShellWindow()), int(user.GetDesktopWindow())}
        for hwnd, *_ in native_windows():
            if win32gui.GetClassName(hwnd).casefold() in (
                "workerw", "shell_traywnd", "shell_secondarytraywnd"
            ):
                handles.add(hwnd)
        require(len(handles) >= 3, "Desktop/taskbar identities not available")
        keys = Mock()
        guarded = WindowsExecutor(AppConfig(), Mock(is_still_target=Mock(return_value=True)), keys=keys)
        try:
            for hwnd in handles:
                require(not is_closable_window(hwnd), "Shell window allowed")
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                ctx = WindowContext(hwnd, pid, "explorer.exe", "Shell fixture", AppKind.OTHER, None)
                result = guarded.execute(parser.parse("đóng cửa sổ", ctx, Mode.COMMAND), ctx)
                require(result.error_code == "desktop_close_blocked", "Executor did not block shell")
            require(not keys.mock_calls, "Shell received shortcut")
        finally:
            guarded.shutdown()
        return f"{len(handles)} real desktop/taskbar HWNDs blocked; key sender replaced by a mock"

    def notepad(unsaved):
        path = folder / ("unsaved.txt" if unsaved else "saved.txt")
        path.write_text("", encoding="utf-8")
        process = subprocess.Popen(["notepad.exe", str(path)])
        processes.append(process)
        hwnd = wait_for(lambda: next((w[0] for w in native_windows()
                                      if w[1] == process.pid and path.name in w[2]), 0))
        require(is_closable_window(hwnd), "Notepad blocked")
        activate(hwnd)
        if unsaved:
            input_ops.type_unicode("Unsaved fixture text.")
        execute(parser, executor, hwnd, "đóng cửa sổ")
        if unsaved:
            popup = wait_for(lambda: next(
                (w[0] for w in native_windows()
                 if w[1] == process.pid and win32gui.GetClassName(w[0]) == "#32770"), 0))
            require(win32gui.IsWindow(hwnd), "Unsaved document was discarded")
            require(win32gui.IsWindow(popup), "No save confirmation")
            require(path.read_text(encoding="utf-8") == "", "Fixture was unexpectedly saved")
            return "Unsaved Notepad remains open with the application's save confirmation"
        wait_for(lambda: not win32gui.IsWindow(hwnd))
        require(process.wait(timeout=5) == 0, "Notepad did not exit cleanly")
        return "Saved fixture closes through Controller/parser/executor and actual Alt+F4"

    try:
        runner.check("desktop_taskbar_blocked", blocked_shells)
        runner.check("saved_notepad_closes", lambda: notepad(False))
        runner.check("unsaved_notepad_prompts", lambda: notepad(True))
    finally:
        executor.shutdown()
        for process in processes:
            if process.poll() is None:
                process.terminate()  # Only this harness's disposable Notepad processes.
                process.wait(timeout=5)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for widget in app.topLevelWidgets():
            if isValid(widget):
                delete(widget)
        delete(app)
    report = [vars(check) for check in runner.checks]
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Evidence:", folder)
    return int(any(check.status != "pass" for check in runner.checks))


if __name__ == "__main__":
    raise SystemExit(main())
