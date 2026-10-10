"""Regression for the native Windows build's webrtcvad distribution mismatch."""

import ast
import importlib.util
import os
import runpy
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_webrtcvad_hook_collects_the_installed_wheels_distribution(monkeypatch):
    calls = []
    for name in ("PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks"):
        module = types.ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    hooks = sys.modules["PyInstaller.utils.hooks"]
    def copy_metadata(name):
        calls.append(name)
        return [("synthetic-dist-info", "webrtcvad_wheels.dist-info")]
    hooks.copy_metadata = copy_metadata
    result = runpy.run_path(str(ROOT / "packaging" / "hooks" / "hook-webrtcvad.py"))
    assert calls == ["webrtcvad-wheels"]
    assert result["hiddenimports"] == ["_webrtcvad"]
    assert result["datas"] == [("synthetic-dist-info", "webrtcvad_wheels.dist-info")]


def test_spec_selects_project_hooks():
    tree = ast.parse((ROOT / "packaging" / "voicepilot.spec").read_text(encoding="utf-8"))
    analysis = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "Analysis")
    hookspath = next(keyword.value for keyword in analysis.keywords if keyword.arg == "hookspath")
    assert isinstance(hookspath, ast.List) and hookspath.elts
    assert "hooks" in ast.unparse(hookspath)


def test_windows_build_does_not_collect_unrelated_poppler_dlls(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "codex-native" / "poppler"))
    spec = importlib.util.spec_from_file_location("build_support", ROOT / "tools" / "build_support.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    python, windows = tmp_path / "Python312", tmp_path / "Windows"
    result = module.windows_build_path(python, windows).split(os.pathsep)
    assert str(windows / "System32") in result
    assert str(python / "DLLs") in result
    assert os.environ["PATH"] not in result


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 regression")
def test_build_python_output_handles_vietnamese_paths(monkeypatch):
    # Reproduce the redirected cp1252 environment without running pip or the build.
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")
    script = (ROOT / "tools" / "build.ps1").read_text(encoding="utf-8")
    prelude = script[script.index("$ErrorActionPreference ="):script.index("$Root =")]
    command = (prelude + "\n& '" + sys.executable.replace("'", "''")
               + "' -c \"import sys; print(sys.flags.utf8_mode, sys.stdout.encoding)\"")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                            capture_output=True, timeout=20, check=True)
    assert result.stdout.decode("utf-8-sig").strip() == "1 utf-8"


def test_bundle_collects_version_metadata_and_runtime_license_texts(monkeypatch):
    monkeypatch.setenv("PATH", os.environ["PATH"])
    monkeypatch.setattr(sys, "path", sys.path.copy())
    calls = []
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda *args, **kwargs: []
    hooks.collect_dynamic_libs = lambda *args, **kwargs: []
    def copy_metadata(name):
        calls.append(name)
        return [("synthetic-" + name, name + ".dist-info")]
    hooks.copy_metadata = copy_metadata
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    source = (ROOT / "packaging" / "voicepilot.spec").read_text(encoding="utf-8")
    # Stop before Analysis so no native build or model is needed.
    namespace = {"SPECPATH": str(ROOT / "packaging")}
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    exec(compile(source.split("a = Analysis(")[0], "voicepilot.spec", "exec"), namespace)
    assert "voicepilot" in calls  # --version was 0.0.0+dev in the first frozen build.
    assert {"SpeechRecognition", "sherpa-onnx-core", "PySide6-Essentials", "cffi"} <= set(calls)


def test_build_rejects_unused_sphinx_models_reintroduced_by_hooks(monkeypatch):
    monkeypatch.setenv("PATH", os.environ["PATH"])
    monkeypatch.setattr(sys, "path", sys.path.copy())
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda *args, **kwargs: []
    hooks.collect_dynamic_libs = lambda *args, **kwargs: []
    hooks.copy_metadata = lambda name: []
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    analysis = types.SimpleNamespace(
        binaries=[("onnxruntime.dll", "synthetic.dll", "BINARY")],
        datas=[("speech_recognition/pocketsphinx-data/model.bin", "synthetic.bin", "DATA")],
        pure=[], scripts=[],
    )
    namespace = {"SPECPATH": str(ROOT / "packaging"), "Analysis": lambda *args, **kwargs: analysis,
                 "PYZ": lambda *args: None, "EXE": lambda *args, **kwargs: None,
                 "COLLECT": lambda *args, **kwargs: None}
    with pytest.raises(SystemExit, match="pocketsphinx"):
        runpy.run_path(str(ROOT / "packaging" / "voicepilot.spec"), init_globals=namespace)
