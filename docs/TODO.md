# VoicePilot — việc còn lại trước khi phát hành

Tài liệu này chỉ liệt kê những gì còn mở. Mục đã xong được ghi ở phần "Đã xác minh" để người sau biết nguồn.

## 1. Cần làm trên máy Windows (không kiểm tra được trên Linux)

- [ ] Tạo `.venv` sạch với Python 3.12 x64 và cài `requirements.lock`. Sau đó chạy `pip freeze` và cập nhật file khóa. Hiện file này được tạo từ metadata PyPI bằng `uv` (cho target Windows cp312), chưa từng cài thật.
- [ ] `onnxruntime` một bản duy nhất: `sherpa-onnx-core` 1.13.8 đóng gói `onnxruntime.dll` (bản 1.28.2) trong `sherpa_onnx\lib`. Trên Windows, kiểm tra `import sherpa_onnx`, nạp Gipformer và nạp giọng TTS trong cùng một tiến trình, rồi nhận kết quả đúng. Nếu lỗi nạp DLL hoặc kết quả sai, cần đổi cách đóng gói.
- [ ] Tải model bằng `tools\download_models.py` từ máy có mạng. Archive TTS (67 MB, GitHub release) phải khớp SHA-256 trong `models.toml`; giá trị này đã được tính từ bản tải thật trên Linux ngày 2026-10-09. Gipformer vẫn cần ghi SHA-256 của `tokens.txt` (xem mục 3).
- [ ] Kiểm tra HTTPS qua `HTTPS_PROXY` và chứng chỉ CA của Python trên Windows khi tải model.
- [ ] `tools\build.ps1` chạy được trong đường dẫn có dấu tiếng Việt và khoảng trắng; chạy bằng Windows PowerShell 5.1; chạy với `-ExecutionPolicy Bypass`.
- [ ] PyInstaller (`packaging/voicepilot.spec`):
  - `onnxruntime.dll` và `sherpa-onnx-c-api.dll` nằm trong `_internal\sherpa_onnx\lib` (spec dừng build nếu thiếu `onnxruntime.dll`);
  - `espeak-ng-data` không nằm trong bản build: nó đi kèm archive giọng trong `models\tts\`, được `build.ps1` chép cạnh `VoicePilot.exe`;
  - các DLL PortAudio trong `_sounddevice_data` có trong bản build;
  - `flac-win32.exe` có trong `speech_recognition/`;
  - `pocketsphinx-data` đã bị loại (kiểm tra kích thước bản build; ước tính khoảng 30 MB);
  - pywin32 (`pythoncom`, `pywintypes`, `win32com`) import được trong bản đóng gói;
  - plugin `qsvg` của Qt có mặt để icon SVG hiển thị ở khay.
- [ ] `tools/make_icon.py`: đã kiểm tra trên Linux với PySide6 6.12.0 (tạo ICO 256×256 hợp lệ). Kiểm tra lại trên Windows.
- [ ] Nghe thử chất lượng giọng sherpa espeak-ng-data 2023: câu dài, số, tên riêng, dấu câu; kiểm tra log không có cảnh báo "Wrong version of espeak-ng-data".
- [ ] Chạy `tools\benchmark_stt.py` trên 30 câu ghi âm thật, mỗi backend một lần; cập nhật `docs/BENCHMARKS.md` mục 6.
- [ ] Chạy kịch bản `docs/DEMO.vi.md`: 15 chức năng, ba vòng với 10 chức năng ổn định nhất.
- [ ] Chạy ZIP đóng gói trên máy sạch (không Python), có và không có mạng (chế độ Gipformer không cần mạng).

## 2. Giấy phép và pháp lý

- [ ] `LICENSE` đã thay bằng GPL-3.0 đầy đủ, lấy từ `/usr/share/common-licenses/GPL-3` (35 149 byte, SHA-256 `3972dc97…36986`). Không truy cập được gnu.org từ môi trường build, nên chưa so sánh byte với bản chính thức. So sánh trước khi phát hành.
- [ ] `espeak-ng`: đã xác nhận đầu tệp COPYING là GPL phiên bản 3. Cần xác nhận điều khoản "hoặc bất kỳ phiên bản sau" trong header mã nguồn. Cũng cần xác nhận phiên bản espeak-ng được biên dịch trong `sherpa-onnx-core` 1.13.8 (đã thấy chuỗi espeak-ng trong thư viện native, chưa xác định phiên bản).
- [ ] `flac-win32.exe` (đi kèm SpeechRecognition) theo GPL-2.0. Cần xem xét (có thể cần người có chuyên môn pháp lý) xem việc gói chung trong ZIP GPL-3.0 có ổn không. Hiện được ghi trong `THIRD_PARTY_NOTICES.md`.
- [ ] Piper voice `vi_VN-vais1000-medium` huấn luyện từ giọng "lessac" (tiếng Anh). Điều khoản dữ liệu của giọng gốc chưa được kiểm chứng.
- [ ] Metadata PyPI của `cffi` ghi `MIT-0` (có thể là lỗi, giấy phép thực tế là MIT); `pycparser` không có metadata giấy phép. Kiểm tra lại trong `THIRD_PARTY_NOTICES.md`.

## 3. Đã xác minh (nguồn và ngày)

Ngày kiểm chứng: 2026-10-09.

**Gipformer 1.5 68M INT8**
- Repo Hugging Face: `g-group-ai-lab/gipformer1.5-68M-rnnt`. Giấy phép MIT (model card và README).
- Tệp INT8: `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, `tokens.txt`. Commit chứa tệp: `54fa79b1fb790422fa27b2f1a771161f9521ca48`. Head hiện tại của `main` là `dd9227d` và không thay đổi các tệp này.
- SHA-256 (LFS oid): encoder `b528768…9408c`, decoder `e0a156b5…3a101`, joiner `12636559…551ac`. Mỗi giá trị đọc hai lần, khớp.
- Tham số sherpa-onnx: `sample_rate=16000`, `feature_dim=80`, `decoding_method="greedy_search"` (`infer_onnx.py` trong repo `ggroup-ai-lab/gipformer`, commit `main`). Ghi chú: sherpa-onnx ≥ 1.13 cần cả `sherpa-onnx-core`.

**Piper `vi_VN-vais1000-medium`**
- Archive đang dùng: `https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-vi_VN-vais1000-medium.tar.bz2`, 67 154 040 byte, SHA-256 `fa1367710767d36ed5cf13b4a449e20c35ffd12791c2e47c2e64142bfa55551a` (tải lại và so khớp ngày 2026-10-09). Thư mục gốc `vits-piper-vi_VN-vais1000-medium/` chứa `vi_VN-vais1000-medium.onnx` (63 149 198 byte), `tokens.txt`, `espeak-ng-data/`, `MODEL_CARD`.
- Giấy phép: MIT (Piper voices); dữ liệu VAIS-1000 CC BY 4.0 (theo `MODEL_CARD`). Sample rate 22050 Hz, một người nói.
- Đã chạy thật trên Linux với sherpa-onnx 1.13.8: câu hai câu (87 ký tự) sinh 4.53 s âm thanh; mỗi câu gọi `generate` riêng vì gọi một lần với callback chỉ trả về câu đầu (đã ghi trong `tts/piper.py`).

**Đã giải quyết (2026-10-09)**
- Xung đột hai onnxruntime trong một tiến trình (piper-tts kéo `onnxruntime` 1.28.0, sherpa-onnx-core bundle 1.28.2): TTS chuyển sang `sherpa_onnx.OfflineTts`. `piper-tts` và `onnxruntime` đã bỏ khỏi `pyproject.toml` và `requirements.lock`. Chỉ còn một onnxruntime (của sherpa-onnx-core). Việc kiểm tra trên Windows vẫn còn ở mục 1.
- Cờ `--selftest`: `app.py` đã hỗ trợ (nạp cấu hình, parser, kiểm tra file model, import, thoát 0/1, ghi `logs\selftest.txt`). `tools/build.ps1` chạy nó với thư mục cấu hình tạm và không đặt `VOICEPILOT_MODELS_DIR`, rồi in báo cáo và dừng nếu thất bại.

**Thư viện (PyPI, wheel Windows cp312)**
- `sherpa-onnx` 1.13.8 (`sherpa-onnx-core` chứa `onnxruntime.dll` 1.28.2 và các DLL C API), `sounddevice` 0.5.6 (wheel Windows chứa DLL PortAudio), `webrtcvad-wheels` 2.0.14.post1, `SpeechRecognition` 3.17.0 (wheel chứa `flac-win32.exe` và 30 MB `pocketsphinx-data`), `PySide6-Essentials` 6.12.0, `pywin32` 312, `numpy` 2.5.3, `mss` 10.2.0, `tomli-w` 1.2.0.

**Đã làm trong đợt phát hành này**
- `tools/download_models.py` và `tests/unit/test_tools.py` (46 test, không cần mạng; gồm tải archive, kiểm sha256, giải nén an toàn).
- `tools/benchmark_stt.py`, `docs/BENCHMARKS.md` (kịch bản 30 câu).
- `packaging/voicepilot.spec`, `tools/build.ps1`, `tools/make_icon.py`.
- `.github/workflows/ci.yml` (Windows, Python 3.12, ruff, pytest).
- `README.md`, `docs/COMMANDS.vi.md`, `docs/DEMO.vi.md`, `THIRD_PARTY_NOTICES.md`.

## 4. Cần đồng bộ với các nhóm khác

Đã xong ngày 2026-10-09:
- `docs/COMMANDS.vi.md` đã được đồng bộ với `commands.toml`, và có test kiểm tra độ phủ.
- `ruff check src tests tools` đã sạch.
- `models.toml` nằm ở `src/voicepilot/resources/`. Khóa `dest` là đường dẫn tương đối so với `models/`.

Trạng thái bàn giao và các câu hỏi còn mở: xem `docs/HANDOFF.md`.

## 5. Giới hạn đã biết của công cụ

- `benchmark_stt.py` đo độ trễ STT và phân tích lệnh, không đo độ trễ đến khi hiện phản hồi trên màn hình.
- Cột RAM đỉnh của benchmark chỉ chính xác khi chạy mỗi backend trong một lần gọi riêng.
- Cột `reference` không có trong đặc tả ban đầu (`file, expected_intent, expected_params_json, context`), nhưng cần để tính WER. Không có cột này thì WER và tỷ lệ khớp chính xác ghi "n/a".
