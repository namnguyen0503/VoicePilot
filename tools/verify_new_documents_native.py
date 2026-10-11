"""Observe actual blank Office documents and Chromium windows in disposable fixtures."""

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from verify_features import (
    Runner,
    activate,
    browser_fixture_server,
    browser_fixture_window,
    browser_page_tabs,
    execute,
    make_executor,
    native_windows,
    require,
    wait_for,
)

from voicepilot.windows.context import process_basename


def office_suite(folder):
    import pythoncom
    import win32com.client

    from voicepilot.windows import input as input_ops

    runner = Runner("windows_office_blank")
    pythoncom.CoInitialize()
    parser, executor = make_executor()
    for progid, exe, collection_name, phrase, suffix in [
        ("Word.Application", "winword.exe", "Documents", "tạo tài liệu Word mới", ".docx"),
        ("Excel.Application", "excel.exe", "Workbooks", "tạo bảng tính mới", ".xlsx"),
        ("PowerPoint.Application", "powerpnt.exe", "Presentations", "tạo bài trình chiếu mới", ".pptx"),
    ]:
        app = collection = fixture = None
        owned = []
        initial = 0

        def check(
            progid=progid, exe=exe, collection_name=collection_name, phrase=phrase, suffix=suffix, owned=owned
        ):
            nonlocal app, collection, fixture, initial
            app = win32com.client.DispatchEx(progid)
            collection = getattr(app, collection_name)
            initial = int(collection.Count)
            app.Visible = True
            fixture = collection.Add()
            owned.append(fixture)
            path = folder / ("Fixture-" + exe + suffix)
            if exe == "winword.exe":
                fixture.Content.Text = "Keep this fixture text."
                fixture.SaveAs2(str(path), 16)
            elif exe == "excel.exe":
                fixture.Worksheets.Item(1).Range("A1").Value = "Keep this fixture cell."
                fixture.SaveAs(str(path), 51)
            else:
                fixture.Slides.Add(1, 12).Shapes.AddTextbox(
                    1, 10, 10, 200, 50
                ).TextFrame.TextRange.Text = "Keep slide."
                fixture.SaveAs(str(path), 24)
            for home in (False, True):
                if exe == "winword.exe":
                    fixture.Activate()
                elif exe == "excel.exe":
                    fixture.Activate()
                else:
                    fixture.Windows.Item(1).Activate()
                hwnd = int(
                    wait_for(
                        lambda: next(
                            (
                                w[0]
                                for w in native_windows()
                                if process_basename(w[3]).casefold() == exe and fixture.Name in w[2]
                            ),
                            0,
                        )
                    )
                )
                activate(hwnd)
                if home:
                    input_ops.send_hotkey("alt", "f")
                    time.sleep(0.2)
                before = int(collection.Count)
                execute(parser, executor, hwnd, "tạo file mới" if home else phrase)
                wait_for(lambda before=before: int(collection.Count) == before + 1)
                new = (
                    app.ActiveDocument
                    if exe == "winword.exe"
                    else app.ActiveWorkbook
                    if exe == "excel.exe"
                    else app.ActivePresentation
                )
                owned.append(new)
                require(str(new.Name) != str(fixture.Name), "No new document")
                if exe == "winword.exe":
                    require(not new.Content.Text.strip(), "New Word document is not blank")
                    require(
                        fixture.Content.Text.strip() == "Keep this fixture text.", "Existing Word changed"
                    )
                elif exe == "excel.exe":
                    require(new.Worksheets.Item(1).UsedRange.Value is None, "New Excel workbook is not blank")
                    require(
                        fixture.Worksheets.Item(1).Range("A1").Value == "Keep this fixture cell.",
                        "Existing Excel changed",
                    )
                else:
                    require(new.Slides.Count <= 1, "New presentation copied slides")
                    require(fixture.Slides.Count == 1, "Existing presentation changed")
                require(str(new.Path or "") == "", "New document was unexpectedly saved")
                runner.check(
                    exe + ("_home_observed" if home else "_editor_observed"),
                    lambda: "Exactly one unsaved blank; existing fixture preserved",
                )
                # The only documents closed here were created by this harness.
                if exe == "powerpnt.exe":
                    new.Saved = True
                    new.Close()
                else:
                    new.Close(False)
                owned.remove(new)
            return "Home and editor create one blank document each"

        try:
            runner.check(exe, check)
            print(exe, runner.checks[-1].status, runner.checks[-1].detail, flush=True)
        finally:
            for doc in reversed(owned):
                with contextlib.suppress(Exception):
                    if exe == "powerpnt.exe":
                        doc.Saved = True
                        doc.Close()
                    else:
                        doc.Close(False)
            if app is not None and initial == 0:
                with contextlib.suppress(Exception):
                    app.Quit()
            owned.clear()
            fixture = collection = app = None
    executor.shutdown()
    pythoncom.CoUninitialize()
    return runner.checks


def browser_suite(folder, name):
    import winreg

    runner = Runner("windows_" + name + "_windows")
    executable = "msedge.exe" if name == "edge" else "chrome.exe"
    path = None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        with contextlib.suppress(OSError):
            with winreg.OpenKey(
                hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + executable
            ) as key:
                path = winreg.QueryValue(key, None)
    require(path and Path(path).is_file(), "Browser not installed")
    server = browser_fixture_server()
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    profile = folder / name
    process = subprocess.Popen(
        [
            path,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-features=msImplicitSignin",
            f"--remote-debugging-port={port}",
            f"http://127.0.0.1:{server.server_port}/first",
        ]
    )
    parser, executor = make_executor()

    def targets():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
            return browser_page_tabs(json.load(response))

    def windows():
        return [w for w in native_windows() if w[1] == pid
                and process_basename(w[3]).casefold() == executable]

    pid = 0
    try:
        hwnd = int(
            wait_for(lambda: browser_fixture_window(native_windows(), executable, server.server_port), 15)
        )
        pid = next(w[1] for w in native_windows() if w[0] == hwnd)

        def tab():
            before = len(targets())
            execute(parser, executor, hwnd, "tạo tab mới")
            wait_for(lambda: len(targets()) == before + 1)
            return "One additional page target"

        runner.check("new_tab", tab)

        def new_window():
            before = {w[0] for w in windows()}
            execute(parser, executor, hwnd, "mở cửa sổ mới")
            wait_for(lambda: len({w[0] for w in windows()} - before) == 1)
            return "One additional native browser window"

        runner.check("new_window", new_window)

        def private():
            before = {w[0] for w in windows()}
            execute(parser, executor, hwnd, "mở cửa sổ ẩn danh")
            new_hwnd = int(wait_for(lambda: next(iter({w[0] for w in windows()} - before), 0)))
            # Opening a normal window cannot satisfy the observed private-mode UI.
            import pythoncom

            from voicepilot.windows.office import _Accessible

            pythoncom.CoInitialize()
            labels = []

            def read(node, depth=0):
                if depth > 12 or len(labels) > 1000 or node.state & 0x18000:
                    return
                labels.append(node.name.casefold())
                for child in node.children():
                    read(child, depth + 1)

            try:
                with _Accessible.root(new_hwnd) as root:
                    read(root)
                require(
                    any("inprivate" in t or "incognito" in t or "ẩn danh" in t for t in labels),
                    "New window does not show private mode",
                )
            finally:
                pythoncom.CoUninitialize()
            return "One additional window with InPrivate/Incognito accessibility label"

        runner.check("private_window", private)
        print(name, [(c.name, c.status, c.detail) for c in runner.checks], flush=True)
    finally:
        executor.shutdown()
        # Chromium's unique fixture profile owns this PID, never the personal browser.
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        elif process.poll() is None:
            process.terminate()
        server.shutdown()
        server.server_close()
    return runner.checks


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder = Path(__file__).resolve().parents[1] / "build" / ("new-documents-native-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    os.environ["VOICEPILOT_CONFIG_DIR"] = str(folder / "VoicePilot")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete, isValid

    app = QApplication.instance() or QApplication([])
    checks = []
    try:
        checks.extend(office_suite(folder))
        for name in ("edge", "chrome"):
            checks.extend(browser_suite(folder, name))
    finally:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for widget in app.topLevelWidgets():
            if isValid(widget):
                delete(widget)
        delete(app)
    report = [vars(check) for check in checks]
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Evidence:", folder)
    return int(any(check.status != "pass" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
