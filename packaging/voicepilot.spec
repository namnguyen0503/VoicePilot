# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the VoicePilot portable build (onedir, windowed).

Run through tools/build.ps1, which first renders build/icon.ico from the SVG.
Models are NOT bundled here; build.ps1 copies models/ next to VoicePilot.exe, outside _internal.
Speech synthesis and recognition both run on sherpa-onnx, which ships its own onnxruntime.dll.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is provided by PyInstaller
sys.path.insert(0, str(ROOT / "tools"))
from build_support import windows_build_path

if sys.platform == "win32":
    os.environ["PATH"] = windows_build_path(Path(sys.base_prefix), Path(os.environ["SystemRoot"]))
SRC = ROOT / "src"
ICON = ROOT / "build" / "icon.ico"
ENTRY = SRC / "voicepilot" / "__main__.py"

if not ICON.is_file():
    raise SystemExit(f"Missing {ICON}: run tools/make_icon.py first (tools/build.ps1 does this).")

# Package resources read through importlib.resources (defaults.toml, commands.toml, models.toml, style.qss, icon.svg).
datas = [
    (str(path), "voicepilot/resources")
    for path in sorted((SRC / "voicepilot" / "resources").glob("*"))
    if path.is_file()
]
# Keep version metadata and wheel license texts beside the frozen runtime.
datas += copy_metadata("voicepilot")
for requirement in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
    if "==" in requirement and not requirement.lstrip().startswith("#"):
        datas += copy_metadata(requirement.split("==", 1)[0].strip())

binaries = []

# sherpa-onnx: the Python extension lives in sherpa_onnx/lib/, together with the native DLLs
# from the sherpa-onnx-core package (onnxruntime.dll, sherpa-onnx-c-api.dll). The espeak-ng data
# for TTS is not part of the wheel; it comes with the voice archive in models/.
binaries += collect_dynamic_libs("sherpa_onnx")

# sounddevice loads PortAudio through ctypes from _sounddevice_data/portaudio-binaries/*.dll,
# so the DLLs must be data files, not analysed binaries.
datas += collect_data_files("_sounddevice_data")

# SpeechRecognition: the Google backend converts audio with flac-win32.exe, which must ship.
# The wheel also carries ~30 MB of pocketsphinx models that VoicePilot never uses; they are excluded.
datas += collect_data_files("speech_recognition", excludes=["pocketsphinx-data/**"])

hiddenimports = [
    # Qt modules used by the app; PySide6-Essentials has no WebEngine or Quick modules anyway.
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "PySide6.QtNetwork",
    # pywin32 COM (PowerPoint automation) and the Windows message helpers.
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
    "win32timezone",
    # Voice activity detection (C extension module named webrtcvad).
    "webrtcvad",
]

excludes = [
    "tkinter",
    "unittest",
    "pytest",
    "pydoc",
    "IPython",
    "matplotlib",
    "torch",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtPdf",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtCharts",
    "PySide6.Qt3DCore",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
]

a = Analysis(  # noqa: F821 - Analysis is provided by PyInstaller
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# The single onnxruntime for the whole process comes from sherpa-onnx-core. Fail the build if it is missing.
if not any(Path(dest).name.lower() == "onnxruntime.dll" for dest, _src, _kind in a.binaries):  # noqa: F821
    raise SystemExit("onnxruntime.dll (sherpa_onnx/lib) was not collected; check the sherpa-onnx-core install.")
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoicePilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VoicePilot",
)
