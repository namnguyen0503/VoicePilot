#!/usr/bin/env python3
"""Verify a relocated Windows ZIP without repository, venv or external models on its PATH.

This checks isolation on the current host, not a clean machine without Python installed.
Reports, extracted files and loaded DLL paths are retained in a new evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

RUNTIME_CHECKS = (
    "runtime: ASR and TTS models loaded in one process",
    "runtime: synthetic TTS -> ASR -> screenshot intent",
    "runtime: unbiased dictation returned text",
)


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def extract_bundle(archive_path: Path, destination: Path) -> Path:
    """Validate the entire archive before extraction into a new directory."""
    require(not destination.exists(), "Extraction directory already exists")
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            normalized = entry.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            require(not path.is_absolute() and ".." not in path.parts and ":" not in normalized,
                    "Unsafe path in ZIP")
            require(path.parts and path.parts[0] == "VoicePilot", "Unexpected ZIP root")
            require((entry.external_attr >> 16) & 0o170000 != 0o120000, "Symlink in ZIP")
        require(archive.testzip() is None, "ZIP CRC failed")
        archive.extractall(destination)
    executable = destination / "VoicePilot" / "VoicePilot.exe"
    require(executable.is_file(), "VoicePilot.exe is missing")
    return executable


def isolated_environment(config: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "VOICEPILOT_", "CONDA_"))
                   and key.upper() != "VIRTUAL_ENV" and not key.lower().endswith("_proxy")}
    windows = Path(os.environ["SystemRoot"])
    environment.update(PATH=os.pathsep.join(str(path) for path in (windows / "System32", windows)),
                       VOICEPILOT_CONFIG_DIR=str(config), QT_QPA_PLATFORM="offscreen",
                       HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="")
    return environment


def loaded_modules(pid: int) -> set[str]:
    """Snapshot native module paths with guarded Windows APIs."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.K32EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    kernel.K32EnumProcessModulesEx.restype = wintypes.BOOL
    kernel.K32GetModuleFileNameExW.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                            wintypes.LPWSTR, wintypes.DWORD]
    kernel.K32GetModuleFileNameExW.restype = wintypes.DWORD
    handle = kernel.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
    if not handle:
        return set()  # The child may have exited between poll and snapshot.
    try:
        modules = (ctypes.c_void_p * 2048)()
        needed = wintypes.DWORD()
        if not kernel.K32EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules),
                                             ctypes.byref(needed), 3):
            return set()
        require(needed.value <= ctypes.sizeof(modules), "Native module snapshot was truncated")
        paths = set()
        for module in modules[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
            buffer = ctypes.create_unicode_buffer(32768)
            if kernel.K32GetModuleFileNameExW(handle, module, buffer, len(buffer)):
                paths.add(buffer.value)
        return paths
    finally:
        kernel.CloseHandle(handle)


def run_executable(executable: Path, folder: Path, *, runtime: bool) -> tuple[int, set[str]]:
    """Run without a window and collect module observations while the child is alive."""
    config = folder / "cấu hình thử"
    environment = isolated_environment(config)
    if not runtime:
        environment["VOICEPILOT_MODELS_DIR"] = str(folder / "missing-models")
    command = [str(executable), "--selftest-runtime" if runtime else "--selftest"]
    modules: set[str] = set()
    with (folder / "stdout.txt").open("wb") as stdout, (folder / "stderr.txt").open("wb") as stderr:
        process = subprocess.Popen(command, cwd=folder, env=environment, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 120
            while process.poll() is None:
                modules.update(loaded_modules(process.pid))
                if time.monotonic() >= deadline:
                    raise TimeoutError("Portable selftest exceeded 120 seconds")
                time.sleep(0.1)
            return process.returncode, modules
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def check_runtime(report_path: Path, code: int, modules: set[str], app: Path, windows: Path) -> None:
    """Require native inference evidence and DLLs from the bundle or Windows."""
    require(code == 0, f"Portable runtime exited {code}")
    report = report_path.read_text(encoding="utf-8")
    require("SELFTEST OK" in report and "[FAIL]" not in report, "Portable selftest did not pass")
    for expected in RUNTIME_CHECKS:
        require(f"[OK] {expected}" in report, f"Runtime evidence missing: {expected}")
    for expected in ("python312.dll", "onnxruntime.dll"):
        require(any(Path(module).name.lower() == expected for module in modules),
                f"Bundled native runtime was not observed: {expected}")
    for module in modules:
        path = Path(module).resolve()
        require(path.is_relative_to(app.resolve()) or path.is_relative_to(windows.resolve()),
                f"External native module: {module}")


def check_missing_models(report_path: Path, code: int) -> None:
    report = report_path.read_text(encoding="utf-8")
    require(code == 1 and "[FAIL] model file " in report and ": MISSING, expected at " in report
            and "SELFTEST FAILED" in report, "Missing models were accepted")


def verify(archive: Path, folder: Path) -> list[dict[str, str]]:
    checks = []
    executable = extract_bundle(archive, folder / "bản giải nén có dấu")
    for name, runtime in (("relocated_runtime_isolation", True), ("missing_models_rejected", False)):
        case = folder / name
        case.mkdir()
        try:
            code, modules = run_executable(executable, case, runtime=runtime)
            (case / "loaded-modules.json").write_text(json.dumps(sorted(modules), indent=2),
                                                      encoding="utf-8")
            report_path = case / "cấu hình thử" / "logs" / "selftest.txt"
            if runtime:
                check_runtime(report_path, code, modules, executable.parent, Path(os.environ["SystemRoot"]))
            else:
                check_missing_models(report_path, code)
            checks.append({"name": name, "status": "pass"})
        except (AssertionError, OSError, TimeoutError) as exc:
            checks.append({"name": name, "status": "fail", "detail": str(exc)})
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True, help="existing portable ZIP")
    parser.add_argument("--output", type=Path, help="new evidence directory")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Portable runtime verification requires Windows")
    archive = args.zip.resolve()
    if not archive.is_file():
        parser.error("--zip does not exist")
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VoicePilot" / "verification"
    folder = (args.output or base / ("portable-" + datetime.now().strftime("%Y%m%d-%H%M%S-")
                                    + uuid.uuid4().hex[:8])).resolve()
    if folder.exists():
        parser.error("--output must be a new directory")
    folder.mkdir(parents=True)
    try:
        checks = verify(archive, folder)
    except (AssertionError, OSError, zipfile.BadZipFile) as exc:
        checks = [{"name": "extract_bundle", "status": "fail", "detail": str(exc)}]
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    report = {"timestamp": datetime.now(UTC).isoformat(), "archive": str(archive), "sha256": digest,
              "scope": "isolated PATH and relocated ZIP on current host; not a clean machine",
              "checks": checks}
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for check in checks:
        print(f"{check['status']:5} {check['name']}: {check.get('detail', 'Observed expected behavior')}")
    print(f"Report: {folder / 'report.json'}")
    return int(any(check["status"] != "pass" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
