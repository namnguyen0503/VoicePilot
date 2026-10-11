"""Native Text/focus acceptance harness using a disposable Notepad and fake audio."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", action="store_true", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    folder = args.report_dir.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ["VOICEPILOT_CONFIG_DIR"] = str(folder / "config")
    import win32con
    import win32gui
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from verify_features import activate, edit_text, native_windows

    from voicepilot import app as bootstrap
    from voicepilot.config import load_config
    from voicepilot.contracts import ResultStatus
    from voicepilot.intent.parser import RuleParser

    qt = QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    qt.setStyleSheet(bootstrap._load_style())
    report = {}
    document = folder / ("VoicePilot input " + uuid.uuid4().hex[:8] + ".txt")
    document.write_text("", encoding="utf-8")
    note = subprocess.Popen(["notepad.exe", str(document)])
    vp = None

    def wait(predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            qt.processEvents()
            if value := predicate():
                return value
            time.sleep(0.01)
        raise AssertionError("Native fixture timed out")

    try:
        hwnd = wait(lambda: next((w[0] for w in native_windows() if w[1] == note.pid), 0))
        activate(hwnd)
        recorder, transcriber = Mock(), Mock()
        config = replace(load_config(folder / "unused.toml"), input_policy="text", tts_enabled=False)
        with (
            patch.object(bootstrap, "_audio_components", return_value=(recorder, transcriber, Mock())),
            patch.object(bootstrap, "_make_speaker", return_value=Mock()),
            patch.object(
                bootstrap, "_make_parser", side_effect=lambda c: RuleParser(c.website_aliases, c.app_aliases)
            ),
        ):
            vp = bootstrap.VoicePilot(
                qt, config, windows=True, icon=bootstrap._app_icon(qt), instance_server=QLocalServer()
            )
        vp._listen_requested()
        wait(lambda: vp._text_input.isVisible())
        assert vp._text_input.screen().availableGeometry().contains(vp._text_input.frameGeometry())
        assert vp._input_context.hwnd == hwnd
        assert win32gui.GetForegroundWindow() == int(vp._text_input.winId())
        report["text_opens_and_owns_keyboard"] = True
        vp._listen_requested()
        assert vp._input_context.hwnd == hwnd
        recorder.record.assert_not_called()
        recorder.record_live.assert_not_called()
        report["reopen_preserves_target_and_microphone_is_off"] = True
        vp._controller.toggle_mode()
        composer = vp._text_input
        composer._edit.setText("Xin chào Việt Nam 👋")
        composer.grab().save(str(folder / "text.png"))
        results = []
        vp._controller.result_ready.connect(results.append)
        QTest.keyClick(composer._edit, Qt.Key.Key_Return)
        composer._send.click()
        wait(lambda: results)
        assert len(results) == 1 and results[0].status is ResultStatus.OK, results
        editor = win32gui.FindWindowEx(hwnd, 0, "Edit", None)
        wait(lambda: edit_text(editor) == "Xin chào Việt Nam 👋")
        text = edit_text(editor)
        assert text == "Xin chào Việt Nam 👋", repr(text)
        assert win32gui.GetForegroundWindow() == hwnd
        report["enter_inserts_unicode_once_into_original_target"] = True
        vp._select_input("text")
        wait(lambda: win32gui.GetForegroundWindow() == int(composer.winId()))
        composer._tabs.setCurrentIndex(0)
        wait(lambda: win32gui.GetForegroundWindow() == hwnd)
        assert vp._controller.input_channel == "voice"
        report["return_to_voice_restores_target"] = True
        vp._overlay.grab().save(str(folder / "voice.png"))
        vp._open_manual()
        qt.processEvents()
        manual = vp._manual
        assert manual.isVisible()
        manual._search.setText("tiết kiệm pin")
        QTest.qWait(100)
        assert "Power saver" in manual._browser.toPlainText()
        manual.repaint()
        manual.grab().save(str(folder / "manual.png"))
        viewport = manual._browser.viewport()
        pixel = viewport.grab().toImage().pixelColor(viewport.width() - 20, viewport.height() - 20)
        assert max(pixel.redF(), pixel.greenF(), pixel.blueF()) < 0.3, pixel.name()
        report["manual_navigation_search_and_windows_commands"] = True
        # Save only the disposable fixture, to preserve native text evidence without a prompt.
        win32gui.SendMessage(editor, win32con.EM_SETMODIFY, False, 0)
        document.write_text(text, encoding="utf-8")
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if vp is not None:
            vp.shutdown()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        note.terminate()
        note.wait(timeout=5)
        (folder / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
