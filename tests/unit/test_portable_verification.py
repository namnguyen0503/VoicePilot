"""Portable verification must observe native inference and reject external dependencies."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_portable", Path(__file__).resolve().parents[2] / "tools" / "verify_portable.py"
)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def runtime_report(tmp_path):
    report = tmp_path / "selftest.txt"
    report.write_text("\n".join(f"[OK] {check}" for check in harness.RUNTIME_CHECKS)
                      + "\nSELFTEST OK\n", encoding="utf-8")
    return report


def modules_in(app):
    return {str(app / "_internal" / name) for name in ("python312.dll", "onnxruntime.dll")}


def test_relocated_runtime_accepts_bundled_and_windows_modules(tmp_path):
    app, windows = tmp_path / "VoicePilot", tmp_path / "Windows"
    modules = modules_in(app) | {str(windows / "System32" / "kernel32.dll")}
    harness.check_runtime(runtime_report(tmp_path), 0, modules, app, windows)


def test_runtime_rejects_dll_from_developer_python_installation(tmp_path):
    app, windows = tmp_path / "VoicePilot", tmp_path / "Windows"
    modules = modules_in(app) | {str(tmp_path / "Python312" / "unrelated.dll")}
    with pytest.raises(AssertionError, match="External native module"):
        harness.check_runtime(runtime_report(tmp_path), 0, modules, app, windows)


def test_file_import_selftest_cannot_substitute_for_native_inference(tmp_path):
    app = tmp_path / "VoicePilot"
    report = tmp_path / "selftest.txt"
    report.write_text("[OK] model file exists\nSELFTEST OK\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Runtime evidence missing"):
        harness.check_runtime(report, 0, modules_in(app), app, tmp_path / "Windows")


def test_missing_module_observation_cannot_pass(tmp_path):
    app = tmp_path / "VoicePilot"
    with pytest.raises(AssertionError, match="not observed"):
        harness.check_runtime(runtime_report(tmp_path), 0, set(), app, tmp_path / "Windows")


def test_isolated_environment_drops_external_models_and_python(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "VOICEPILOT_MODELS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "developer"))
    environment = harness.isolated_environment(tmp_path / "config")
    assert not {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "VOICEPILOT_MODELS_DIR"} & environment.keys()
    assert "developer" not in environment["PATH"]
    assert environment["VOICEPILOT_CONFIG_DIR"] == str(tmp_path / "config")


@pytest.mark.parametrize("filename", ["VoicePilot/../../escape", "C:/escape", "/escape"])
def test_zip_validated_before_any_file_is_extracted(tmp_path, filename):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as fixture:
        fixture.writestr("VoicePilot/VoicePilot.exe", b"test executable")
        fixture.writestr(filename, b"must not be extracted")
    destination = tmp_path / "extracted"
    with pytest.raises(AssertionError, match="Unsafe path"):
        harness.extract_bundle(archive, destination)
    assert not destination.exists()


def test_missing_models_requires_the_expected_failure(tmp_path):
    report = tmp_path / "selftest.txt"
    report.write_text("[FAIL] import error\nSELFTEST FAILED\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Missing models were accepted"):
        harness.check_missing_models(report, 1)
    report.write_text("[FAIL] model file asr/encoder: MISSING, expected at nowhere\nSELFTEST FAILED\n",
                      encoding="utf-8")
    harness.check_missing_models(report, 1)
