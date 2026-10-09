# Third-party notices

VoicePilot is distributed under the GNU General Public License v3.0 or later (see `LICENSE`). The portable ZIP also contains the third-party software and speech models listed below. Each entry gives the license that applies to that component. Where a license text is needed, it is in the component's own distribution (the `dist-info` folder or the upstream project).

The versions below are the ones pinned in `requirements.lock`. The list was compiled on 2026-10-09 from PyPI metadata and upstream pages. Items marked **to verify** were not confirmed from the license text itself.

## 1. Runtime components (bundled in `VoicePilot.exe` and the ZIP)

| Component | Version | License | Where it comes from |
|---|---|---|---|
| Python runtime (PyInstaller bundle) | 3.12 | Python Software Foundation License 2.0 | https://www.python.org/ |
| PySide6-Essentials, shiboken6 (Qt for Python) | 6.12.0 | LGPL-3.0-only (VoicePilot uses this option) OR GPL-2.0-only OR GPL-3.0-only | https://pypi.org/project/PySide6-Essentials/ |
| Qt 6 libraries (QtCore, QtGui, QtWidgets, QtSvg, QtNetwork) | 6.12 | LGPL-3.0 | https://www.qt.io/ |
| sherpa-onnx (Python API and `_sherpa_onnx` extension) | 1.13.8 | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| sherpa-onnx-core (native sherpa-onnx C API DLLs, bundles ONNX Runtime 1.28.2, MIT) | 1.13.8 | Apache-2.0 (sherpa-onnx); MIT (ONNX Runtime) | https://github.com/k2-fsa/sherpa-onnx |
| espeak-ng (phonemizer compiled into the sherpa-onnx-core native library) | as bundled, **to verify** | GPL-3.0 (espeak-ng project) | https://github.com/espeak-ng/espeak-ng |
| sounddevice | 0.5.6 | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio (`libportaudio64bit.dll` in the sounddevice Windows wheel) | bundled | PortAudio license (MIT-style) | http://www.portaudio.com/ |
| webrtcvad-wheels (Python binding for WebRTC VAD) | 2.0.14.post1 | MIT (binding); the WebRTC VAD code is BSD-3-Clause (Google) | https://github.com/daanzu/py-webrtcvad-wheels |
| SpeechRecognition | 3.17.0 | BSD-3-Clause | https://github.com/Uberi/speech_recognition |
| flac-win32.exe (FLAC command-line encoder, bundled by SpeechRecognition) | as bundled | GPL-2.0 (full text in the SpeechRecognition `dist-info`, file `LICENSE-FLAC.txt`) | https://xiph.org/flac/ |
| mss (screen capture) | 10.2.0 | MIT | https://github.com/BoboTiG/python-mss |
| pywin32 (win32com, pythoncom, pywintypes) | 312 | Python Software Foundation License | https://github.com/mhammond/pywin32 |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (bundled libraries) | https://numpy.org/ |
| tomli-w | 1.2.0 | MIT | https://github.com/hukkin/tomli-w |
| cffi (sounddevice dependency) | 2.1.1 | MIT (upstream); the PyPI metadata field reads MIT-0, **to verify** | https://github.com/python-cffi/cffi |
| pycparser (cffi dependency) | 3.1 | BSD-3-Clause (upstream); PyPI metadata is empty, **to verify** | https://github.com/eliben/pycparser |
| typing-extensions | 4.16.0 | Python Software Foundation License 2.0 | https://github.com/python/typing_extensions |

Note on GPL components: espeak-ng (GPL-3.0) is compiled into the sherpa-onnx-core native library, and its data files (`espeak-ng-data`) ship in the voice archive below. Because of this VoicePilot as a whole is GPL-3.0-or-later. The Piper voice itself is a model file, not a linked library. The SpeechRecognition FLAC binary is GPL-2.0; it is shipped as a separate program that the application runs, not linked into it.

## 2. Build-time tools (not shipped in the ZIP)

| Tool | Version | License |
|---|---|---|
| PyInstaller (builds the portable folder) | 6.22.3 | GPL-2.0-or-later with a bootloader exception |
| pytest | latest compatible with pytest-qt | MIT |
| pytest-qt | 4.5.0 | MIT |
| ruff | 0.16.10 | MIT |
| psutil (optional, benchmark only) | 7.2.2 | BSD-3-Clause |

## 3. Speech models (downloaded by `tools/download_models.py`, bundled in the ZIP)

### 3.1 Speech recognition: Gipformer 1.5 68M, INT8 ONNX

- Files: `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, `tokens.txt`.
- Source: https://huggingface.co/g-group-ai-lab/gipformer1.5-68M-rnnt (revision `54fa79b1fb790422fa27b2f1a771161f9521ca48`). Upstream code: https://github.com/ggroup-ai-lab/gipformer.
- License: MIT, as stated on the Hugging Face model card and in the upstream repository's LICENSE badge. The MIT notice must be kept with copies of the model.
- Copyright holder: the upstream project names "G-Group AI Lab" as author.

### 3.2 Text-to-speech: Piper voice `vi_VN-vais1000-medium` (sherpa-onnx VITS package)

- Files used: `vi_VN-vais1000-medium.onnx`, `tokens.txt`, `espeak-ng-data/` (from the archive below).
- Source: https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-vi_VN-vais1000-medium.tar.bz2 (tag `tts-models`), archive SHA-256 `fa1367710767d36ed5cf13b4a449e20c35ffd12791c2e47c2e64142bfa55551a`. The voice was converted to the sherpa-onnx format by k2-fsa from the Piper voice at https://huggingface.co/rhasspy/piper-voices (folder `vi/vi_VN/vais1000/medium`).
- License: the Piper voices repository is MIT. The voice's training data is the VAIS-1000 Vietnamese speech synthesis corpus, under **CC BY 4.0**. Attribution: VAIS-1000 Vietnamese Speech Synthesis Corpus, https://ieee-dataport.org/documents/vais-1000-vietnamese-speech-synthesis-corpus , and the licence at https://creativecommons.org/licenses/by/4.0/ .
- The model card states that the voice was fine-tuned from the English "lessac" voice. The terms of that base voice's training data were **not verified** and should be checked before wider distribution.

## 4. Fonts and icons

- `src/voicepilot/resources/icon.svg` is an original vector icon created for this project and is covered by the project license.
- No third-party fonts are bundled. Windows system fonts are used at run time.

## 5. Services used at run time (not bundled)

- Google speech recognition is reached through SpeechRecognition's public web recognition endpoint. It is not a Google Cloud Speech API key or an official SDK. The audio is sent to Google, and Google's terms apply. The endpoint may change without notice.
