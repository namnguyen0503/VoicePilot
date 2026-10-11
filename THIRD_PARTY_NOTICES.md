# Third-party notices

VoicePilot is distributed under the GNU General Public License v3.0 or later (see `LICENSE`). The portable ZIP also contains the third-party software and speech models listed below. Wheel metadata and license texts are retained in `_internal/*dist-info/`. Component source links are listed below; a public release still requires accessible corresponding source and model redistribution permission.

The versions below are pinned in `requirements.lock`. The list was compiled on 2026-10-09 and checked against installed Windows wheels and upstream source on 2026-10-10. Items marked **to verify** remain unresolved. The project's `LICENSE` matches the [GNU Coreutils GPLv3 text](https://raw.githubusercontent.com/coreutils/coreutils/master/COPYING) byte for byte: 35,149 bytes, SHA-256 `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`.

## 1. Runtime components (bundled in `VoicePilot.exe` and the ZIP)

| Component | Version | License | Where it comes from |
|---|---|---|---|
| Python runtime (PyInstaller bundle) | 3.12 | Python Software Foundation License 2.0 | https://www.python.org/ |
| PySide6-Essentials, shiboken6 (Qt for Python) | 6.12.0 | LGPL-3.0-only (VoicePilot uses this option) OR GPL-2.0-only OR GPL-3.0-only | https://pypi.org/project/PySide6-Essentials/ |
| Qt 6 libraries (QtCore, QtGui, QtWidgets, QtSvg, QtNetwork) | 6.12 | LGPL-3.0 | https://www.qt.io/ |
| sherpa-onnx (Python API and `_sherpa_onnx` extension) | 1.13.8 | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| sherpa-onnx-core (native sherpa-onnx C API DLLs, bundles ONNX Runtime 1.28.2, MIT) | 1.13.8 | Apache-2.0 (sherpa-onnx); MIT (ONNX Runtime) | https://github.com/k2-fsa/sherpa-onnx |
| espeak-ng (phonemizer compiled into sherpa-onnx-core) | source recipe: 1.52.0.1, wheel provenance **to verify** | GPL-3.0-or-later | https://github.com/csukuangfj/espeak-ng/tree/ed530aa113046142eb5115cf2fc9157854d0ffe1 |
| sounddevice | 0.5.6 | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio (`libportaudio64bit.dll` in the sounddevice Windows wheel) | bundled | PortAudio license (MIT-style) | http://www.portaudio.com/ |
| webrtcvad-wheels (Python binding for WebRTC VAD) | 2.0.14.post1 | MIT (binding); the WebRTC VAD code is BSD-3-Clause (Google) | https://github.com/daanzu/py-webrtcvad-wheels |
| SpeechRecognition | 3.17.0 | BSD-3-Clause | https://github.com/Uberi/speech_recognition |
| flac-win32.exe (FLAC command-line encoder, bundled by SpeechRecognition) | 1.3.2 (`--version` verified on Windows) | GPL-2.0 (full text in SpeechRecognition `dist-info/licenses/LICENSE-FLAC.txt`) | https://xiph.org/flac/ |
| mss (screen capture) | 10.2.0 | MIT | https://github.com/BoboTiG/python-mss |
| pywin32 (win32com, pythoncom, pywintypes) | 312 | Python Software Foundation License | https://github.com/mhammond/pywin32 |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 (bundled libraries) | https://numpy.org/ |
| tomli-w | 1.2.0 | MIT | https://github.com/hukkin/tomli-w |
| cffi (sounddevice dependency) | 2.1.1 | MIT-0 (MIT No Attribution), verified in the installed wheel and versioned LICENSE | https://raw.githubusercontent.com/python-cffi/cffi/v2.1.1/LICENSE |
| pycparser (cffi dependency) | 3.1 | BSD-3-Clause, verified in the installed wheel's LICENSE and metadata | https://github.com/eliben/pycparser |
| PyWinRT runtime and Windows.Media.Control / Foundation / Foundation.Collections / Media / Storage.Streams projections | 3.2.1 | MIT | https://github.com/pywinrt/pywinrt |
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

### 3.0 Voice activity detection: Silero VAD v6.0, ONNX

- Files: `models/vad/silero-v6/silero_vad.onnx` and upstream `LICENSE`.
- Source: https://github.com/snakers4/silero-vad/releases/tag/v6.0 ; model at
  https://github.com/snakers4/silero-vad/blob/v6.0/src/silero_vad/data/silero_vad.onnx .
- MIT License, copyright (c) 2020-present Silero Team. The full upstream notice is
  retained beside the model. Runs through the existing sherpa-onnx ONNX Runtime;
  no PyTorch or second ONNX Runtime is bundled.

### 3.1 Speech recognition: Gipformer 1.5 68M, INT8 ONNX

- Files: `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, `tokens.txt`.
- Source: https://huggingface.co/g-group-ai-lab/gipformer1.5-68M-rnnt (revision `54fa79b1fb790422fa27b2f1a771161f9521ca48`). Upstream code: https://github.com/ggroup-ai-lab/gipformer.
- License: MIT, as stated on the Hugging Face model card and in the upstream repository's LICENSE badge. The MIT notice must be kept with copies of the model.
- Copyright holder: the upstream project names "G-Group AI Lab" as author.

### 3.2 Text-to-speech: Piper voice `vi_VN-vais1000-medium` (sherpa-onnx VITS package)

- Files used: `vi_VN-vais1000-medium.onnx`, `tokens.txt`, `espeak-ng-data/` (from the archive below).
- Source: https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-vi_VN-vais1000-medium.tar.bz2 (tag `tts-models`), archive SHA-256 `fa1367710767d36ed5cf13b4a449e20c35ffd12791c2e47c2e64142bfa55551a`. The voice was converted to the sherpa-onnx format by k2-fsa from the Piper voice at https://huggingface.co/rhasspy/piper-voices (folder `vi/vi_VN/vais1000/medium`).
- License: the Piper voices repository is MIT. The voice's training data is the VAIS-1000 Vietnamese speech synthesis corpus, under **CC BY 4.0**. Attribution: VAIS-1000 Vietnamese Speech Synthesis Corpus, https://ieee-dataport.org/documents/vais-1000-vietnamese-speech-synthesis-corpus , and the licence at https://creativecommons.org/licenses/by/4.0/ .
- The model card states that the voice was fine-tuned from the English "lessac" voice. Its [base voice model card](https://huggingface.co/rhasspy/piper-voices/raw/main/en/en_US/lessac/medium/MODEL_CARD) links the Lessac Blizzard 2013 dataset to a [research license](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html). Those terms restrict the original dataset to research and limit redistribution. This establishes the dataset's terms, but does not establish permission to redistribute these fine-tuned model weights. Wider model distribution remains unresolved pending clarification from the model publisher or rights holder.

The [sherpa-onnx v1.13.8 build recipe](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/v1.13.8/cmake/espeak-ng-for-piper.cmake)
pins espeak-ng commit `ed530aa113046142eb5115cf2fc9157854d0ffe1` (archive SHA-256
`e4e262cbe34f7fe21f91f1ba3397f2728e1f30eafbae7853f2b753a9ed13f0dd`). Its
[CMake version](https://raw.githubusercontent.com/csukuangfj/espeak-ng/ed530aa113046142eb5115cf2fc9157854d0ffe1/CMakeLists.txt)
is 1.52.0.1 and its [source header](https://raw.githubusercontent.com/csukuangfj/espeak-ng/ed530aa113046142eb5115cf2fc9157854d0ffe1/src/libespeak-ng/speech.c)
grants GPL-3.0-or-later. This verifies the published source recipe; the installed wheel's exact build provenance is still unconfirmed.

## 4. Fonts and icons

- `src/voicepilot/resources/icon.svg` is an original vector icon created for this project and is covered by the project license.
- No third-party fonts are bundled. Windows system fonts are used at run time.

## 5. Services used at run time (not bundled)

- Google speech recognition is reached through SpeechRecognition's public web recognition endpoint. It is not a Google Cloud Speech API key or an official SDK. The audio is sent to Google, and Google's terms apply. The endpoint may change without notice.
