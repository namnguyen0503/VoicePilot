"""Run the custom-command acceptance harness without desktop effects."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def native_checks(folder: Path):
    """Use disposable classic Notepad and isolated browser profiles only."""
    import win32con
    import win32gui
    from PySide6.QtWidgets import QApplication

    from voicepilot.contracts import Mode
    from voicepilot.windows.context import Win32ContextProvider

    spec = importlib.util.spec_from_file_location("custom_native_features", ROOT / "tools/verify_features.py")
    harness = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = harness
    spec.loader.exec_module(harness)
    app = QApplication.instance() or QApplication([])
    runner = harness.Runner("windows-custom")
    reason = harness.desktop_block_reason(Win32ContextProvider().capture())
    if reason:
        checks = [harness.Check("custom_native", "windows-custom", "blocked", 0, reason)]
        harness.write_report(folder, checks)
        return checks

    def rule(ident, phrase, intent, params):
        return dict(
            id=ident,
            name=ident,
            enabled=True,
            patterns=[phrase],
            intent=intent,
            params=params,
            context="any",
            process="",
        )

    items = [
        rule("signature", "ký tên tôi", "DICTATE_TEXT", {"text": "Nam Việt Nam 👋"}),
        rule("select", "chọn hết", "WIN_HOTKEY", {"keys": "ctrl+a"}),
        rule("search", "hỏi google về {topic}", "BROWSER_SEARCH", {"query": "{topic}"}),
        rule("url", "trang riêng {topic}", "BROWSER_OPEN_SITE", {"url": "https://example.com/?q={topic}"}),
    ]
    parser, executor = harness.make_executor(custom_commands=items)
    document = folder / "VoicePilot custom verification.txt"
    document.write_text("", encoding="utf-8")
    process = subprocess.Popen(["notepad.exe", str(document)])
    hwnd = editor = 0
    try:
        hwnd = int(
            harness.wait_for(
                lambda: next(
                    (w[0] for w in harness.native_windows() if w[1] == process.pid and "notepad" in w[3]), 0
                )
            )
        )
        editor = win32gui.FindWindowEx(hwnd, 0, "Edit", None)
        if not editor:
            raise harness.Blocked("Classic Notepad Edit control unavailable")

        def signature():
            harness.execute(parser, executor, hwnd, "ký tên tôi")
            harness.wait_for(lambda: harness.edit_text(editor) == "Nam Việt Nam 👋")
            return "Command-mode template inserts the configured Unicode signature exactly once"

        runner.check("custom_signature", signature)

        def select_all():
            harness.execute(parser, executor, hwnd, "chọn hết")
            harness.execute(parser, executor, hwnd, "ký tên tôi")
            harness.wait_for(lambda: harness.edit_text(editor) == "Nam Việt Nam 👋")
            return "Custom Ctrl+A selects all; next signature replaces text instead of appending"

        runner.check("custom_hotkey", select_all)

        def dictation():
            win32gui.SendMessage(editor, 0x000C, 0, "")
            harness.execute(parser, executor, hwnd, "ký tên tôi", Mode.DICTATION)
            harness.wait_for(lambda: harness.edit_text(editor) == "ký tên tôi")
            return "Dictation does not execute the custom signature"

        runner.check("custom_dictation_isolation", dictation)
    finally:
        executor.shutdown()
        if hwnd:
            if editor:
                win32gui.SendMessage(editor, 0x000C, 0, "")
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
    for browser in ("edge", "chrome"):
        target = folder / browser
        target.mkdir()
        try:
            runner.checks.extend(harness.browser_suite(target, browser, custom_commands=items))
        except harness.Blocked as exc:
            runner.checks.append(harness.Check(browser, "windows-custom", "blocked", 0, str(exc)))
    harness.write_report(folder, runner.checks)
    app.processEvents()
    return runner.checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", action="store_true", help="Verify disposable native Notepad/Edge/Chrome")
    args = parser.parse_args()
    if args.windows:
        folder = Path(os.environ["LOCALAPPDATA"]) / "VoicePilot/verification" / ("custom-" + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        checks = native_checks(folder)
        print(f"Native report: {folder / 'report.md'}")
        return int(any(check.status != "pass" for check in checks))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_custom_commands.py",
            "tests/unit/test_custom_ui.py",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(ROOT / "build" / ("custom-tests-" + uuid.uuid4().hex)),
        ],
        cwd=ROOT,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
