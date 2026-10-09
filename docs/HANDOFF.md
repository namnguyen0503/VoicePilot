# Handoff — VoicePilot v1 (2026-10-09)

Bàn giao từ phiên Claude trên claude.ai sang Claude Code chạy trực tiếp trên laptop của Nam.
Đọc file này trước, sau đó đọc `docs/TODO.md` để xem danh sách việc chi tiết.

## 1. Trạng thái hiện tại

- **Đã code đủ 15 chức năng** theo `docs/PLAN.md`: 6 lệnh PowerPoint, 6 lệnh trình duyệt, mở ứng dụng, chụp màn hình và đọc chính tả. Đã có overlay, tray, cài đặt, hybrid STT, TTS, đóng gói và tài liệu.
- **1039 test pass, ruff sạch.** Chạy ổn định 3 lần liên tiếp, **nhưng chỉ trên Linux** (Python 3.13, phần Windows được thay bằng fake).
  - Unit test: `tests/unit/`
  - Test QA độc lập, viết không dựa trên unit test: `tests/qa/`
- **Chưa chạy lần nào trên Windows thật.** Chưa test micro thật, Google STT thật, COM PowerPoint, SendInput, RegisterHotKey, PyInstaller.
- **Git:** repo đã `git init` nhưng **chưa có commit nào**. Việc đầu tiên nên làm: kiểm tra `git status`, rồi commit baseline (xem mục 6).
- `.github/workflows/ci.yml` không ghi được từ xa vào máy. Nam đã nhận file này và tự đặt vào. Cần kiểm tra file có mặt chưa.
- Model chưa được tải. `tools\download_models.py` sẽ tải về `models\` (thư mục này bị git ignore).
  - Gipformer lấy từ Hugging Face.
  - Giọng TTS lấy từ GitHub release của k2-fsa.
- Lệnh AIO Nam tự chạy: tạo venv, cài `requirements.lock`, chạy `pip install -e .[dev]`, tải model, chạy `--selftest`, chạy pytest, rồi mở app. Hãy hỏi Nam kết quả, hoặc chạy lại các bước tương ứng.

## 2. Cách làm việc đã dùng (giữ nguyên)

Nam muốn **Haiku 5.5 viết code**, còn phiên chính lên kế hoạch, kiểm duyệt và xác minh.

- Subagent nằm trong `.claude/agents/`:
  - `haiku-coder`: code theo danh sách file được giao.
  - `haiku-qa`: tìm lỗi độc lập và ghi test vào `tests/qa/`, đánh dấu `xfail(strict=True)` cho lỗi thật.
- Mỗi lần giao việc phải ghi rõ **danh sách file được sửa**. Không cho hai agent sửa cùng một file.
- Phiên chính luôn tự đọc diff và chạy test trước khi chấp nhận thay đổi.
- Các việc độc lập thì cho chạy nền song song. Đừng gửi tiếp việc cho một agent rồi ngồi chờ nó trong khi các việc khác bị xếp hàng. Lần trước làm vậy khiến phiên trông như bị treo 24 phút.

## 3. Kiến trúc và interface giữa các module

```
Hotkey (windows/hotkeys.py, thread riêng) ──signal──▶ Controller (controller.py, main thread Qt)
  1. context_provider.capture()          ← chụp cửa sổ đích TRƯỚC khi hiện overlay
  2. worker: Recorder.record()            → AudioClip 16 kHz mono int16 (audio/capture.py + audio/vad.py)
  3. HybridTranscriber.transcribe(clip, is_valid, cancel_event)   (stt/hybrid.py; google.py, gipformer.py)
  4. RuleParser.parse(text, ctx, mode)   → Command                 (intent/parser.py + resources/commands.toml)
  5. is_still_target(ctx) (bỏ qua khi ctx.hwnd == 0) → WindowsExecutor.execute(cmd, ctx) → ActionResult
  6. Overlay hiện chữ (ui/overlay.py); PiperSpeaker.speak(message_vi) chỉ ở chế độ Ra lệnh (tts/piper.py)
```

- **Kiểu dữ liệu chung** nằm trong `contracts.py`: `Mode`, `SttBackendMode`, `AudioClip`, `Transcript`, `WindowContext`/`AppKind`, `Intent` (15 lệnh + UNKNOWN), `Command`, `ActionResult`/`ResultStatus`, các Protocol và exception.
- **Khóa trong `Command.params`:**

  | Lệnh | Khóa |
  |---|---|
  | `PPT_SAVE_AS` | `filename` |
  | `BROWSER_OPEN_SITE` | `url`, `site` |
  | `BROWSER_SEARCH` | `query` |
  | `WIN_OPEN_APP` | `app`, `app_name` |
  | `DICTATE_TEXT` | `text` |

  Giá trị tham số luôn giữ nguyên nội dung gốc người nói. Parser dùng span map, ánh xạ vị trí từ chuỗi đã chuẩn hóa về chuỗi gốc.
- **`app.py`:**
  - Ghép toàn bộ thành phần lại với nhau (wiring).
  - Có `--version` và `--selftest`. Selftest ghi thêm báo cáo vào `%LOCALAPPDATA%\VoicePilot\logs\selftest.txt`, vì bản exe dạng windowed không có stdout.
  - Trên hệ điều hành không phải Windows, app dùng context provider và executor giả, không đăng ký hotkey.
- **`paths.py`:** `app_base_dir()` (thư mục chứa exe khi đã đóng gói, thư mục repo khi chạy dev) và `models_dir()` (đổi được qua env `VOICEPILOT_MODELS_DIR`).
- **`config.py`:**
  - Đọc `defaults.toml`, sau đó áp `config.toml` của người dùng lên trên.
  - Đọc file bằng utf-8-sig. Mỗi khóa được kiểm tra riêng: sai kiểu hoặc ngoài khoảng thì chỉ khóa đó dùng giá trị mặc định, các khóa khác giữ nguyên.

## 4. Quyết định đã chốt (khác hoặc chi tiết hơn PLAN)

1. **TTS chạy qua `sherpa_onnx.OfflineTts`**, vẫn dùng giọng Piper `vi_VN-vais1000-medium` (bản k2-fsa chuyển đổi).
   - Lý do: `piper-tts` kéo theo `onnxruntime` pip, trong khi sherpa-onnx-core đã bundle sẵn ORT 1.28.2. Hai DLL `onnxruntime.dll` cùng nằm trong một tiến trình Windows là rủi ro.
   - Kết quả: còn một ORT duy nhất, ZIP nhỏ hơn khoảng 50 MB. Tên module vẫn là `tts/piper.py`.
   - Mỗi câu gọi `generate` riêng.
2. **Lệnh PowerPoint chỉ chạy khi cửa sổ phía trước là PowerPoint.** Nếu không thì trả `NOT_AVAILABLE` "Hãy chuyển sang cửa sổ PowerPoint…". Lý do: tránh sửa ngầm một file đang mở khi người dùng đang ở Chrome.
   - Cửa sổ trình chiếu cũng thuộc `POWERPNT.EXE`, nên vẫn điều khiển được khi đang chiếu.
   - Presentation được chọn theo tiêu đề cửa sổ, vì COM không cho lấy HWND.
3. **Phân loại lệnh theo cửa sổ:**
   - Lệnh tab cần cửa sổ Edge hoặc Chrome ở phía trước.
   - Mở web, tìm kiếm, mở app, chụp màn hình chạy được từ mọi cửa sổ, kể cả khi `ctx.hwnd == 0`. Không có trình duyệt đích thì mở bằng trình duyệt mặc định.
4. **Cụm từ mơ hồ:**
   - Chỉ "tiếp theo" và "quay lại" đổi nghĩa theo cửa sổ (slide hay tab).
   - "đóng" và "mở mới" luôn là lệnh tab.
   - Khóa `context` trong `commands.toml` chỉ dùng để phá thế hòa giữa các lệnh, không dùng để lọc.
5. **Hybrid STT:**
   - Google chạy trước. Gipformer chạy khi Google lỗi, khi kết quả Google không hợp lệ, hoặc sau 2 giây.
   - Kết quả hợp lệ nào về trước thì thắng. Nếu cả hai về cùng lúc thì ưu tiên Google.
   - Mỗi bộ có pool thread riêng: Google 4 worker, Gipformer 1 worker. Nhờ vậy một lượt Google bị treo không làm trễ lượt sau.
   - Ở chế độ chỉ Google hoặc chỉ Offline, `is_valid` không được áp dụng. Controller tự kiểm tra.
6. **Parser:**
   - Lời đệm như "làm ơn", "giúp tôi/mình", "giùm", "nhé", "ạ" bị bỏ ở đầu và cuối câu.
   - "đi" và "với" chỉ bị bỏ ở lệnh ngắn hoặc tên site/app, **không** bỏ trong câu tìm kiếm hay tên tệp. Ví dụ "tìm đường đi" giữ nguyên "đường đi".
   - Tên miền trần phải là hostname hợp lệ và TLD không được là đuôi tệp. "mở notepad.exe" không biến thành URL.
7. **Tên tệp khi lưu:**
   - Chuẩn hóa NFC.
   - Chặn ký tự Cc, Cf, Cs, Co và tên thiết bị dành riêng (cả dạng NFKC, cả phần trước dấu chấm đầu tiên).
   - Đường dẫn đầy đủ không vượt 240 ký tự. Trùng tên thì thêm " (2)", " (3)".
8. **Hotkey:**
   - Giữ `Ctrl+Alt+Space` để nghe, `Ctrl+Alt+D` để đổi chế độ (Nam đã hỏi và đồng ý).
   - Không dùng được kiểu giữ phím để nói (push-to-talk), vì RegisterHotKey không báo khi nhả phím.
   - Trước khi gõ chữ, input nhả Ctrl, Alt, Shift, Win. Riêng Alt có mẹo 0xE8 để menu bar không bật lên.

## 5. Rủi ro lớn nhất trên Windows (kiểm tra theo thứ tự này)

1. **Cài đặt và import:** `pip install -r requirements.lock` trên cp312. `requirements.lock` được tạo bằng `uv` từ metadata PyPI, chưa từng cài thật. Sau khi cài xong thì cập nhật lại bằng `pip freeze`.
2. **sherpa-onnx:** nạp Gipformer và giọng TTS trong cùng một tiến trình, kết quả đúng tiếng Việt.
3. **Hotkey toàn cục:** Ctrl+Alt+Space đăng ký được. Nếu phím đã bị chiếm thì có thông báo lỗi 1409.
4. **Overlay không lấy focus:** gồm `WS_EX_NOACTIVATE`, kéo thả thanh, và focus vẫn ở app đích khi SendInput gõ chữ.
5. **Đọc chính tả:** tiếng Việt và emoji vào đúng trong Word, Notepad, Edge, Chrome. Thử thêm trường hợp cửa sổ chạy quyền admin (UIPI chặn input, phải báo lỗi rõ ràng).
6. **COM PowerPoint:**
   - `GetActiveObject` không tự khởi động PowerPoint.
   - Chuyển slide được ở cả chế độ chỉnh sửa và trình chiếu, có xử lý slide đầu và slide cuối.
   - `SaveAs` với tên tiếng Việt, tên trùng.
   - Timeout 8 giây khi PowerPoint đang bận.
7. **Trình duyệt:** Edge và Chrome mở URL trong tab mới của cửa sổ đang có. Phím tắt tab hoạt động.
8. **Chụp màn hình:** thử ở DPI 125–150%. Qt 6 đã bật per-monitor DPI v2, còn mss gọi `SetProcessDpiAwareness` (sẽ fail vô hại). Overlay phải được ẩn trong ảnh chụp.
9. **Micro thật:** VAD (webrtcvad mode 2, onset 90 ms, im lặng 800 ms) chưa được tinh chỉnh với giọng thật. TTS không được bị micro thu lại.
10. **PyInstaller và `build.ps1`:** đường dẫn có dấu và khoảng trắng; DLL của sherpa, PortAudio, pywin32, plugin qsvg; `--selftest` trên bản exe.

## 5b. Kết quả chạy thật đầu tiên (2026-10-09, máy Nam)

- Cài đặt, tải model, `--selftest` OK. `requirements.lock` khớp `pip freeze` (đã xác minh).
- 3 test lỗi do môi trường Windows (đường dẫn tmp dài, Documents chuyển hướng OneDrive) đã sửa, không phải bug code.
- `Ctrl+Alt+Space` và `Ctrl+Alt+D` đều bị app khác giữ (lỗi 1409; nghi Claude desktop Quick Entry). Máy Nam dùng
  `config.toml` đặt `hotkey_listen = "ctrl+alt+v"`, `hotkey_mode = "ctrl+alt+m"`. Chưa đổi mặc định.
- Cảnh báo "single-instance server unavailable: Access is denied" chỉ thấy khi chạy trong sandbox của Claude Code, không thấy khi Nam tự chạy.

## 6. Việc tiếp theo đề xuất

1. Kiểm tra `git status`. Nếu chưa có commit thì commit baseline: `feat: VoicePilot v1 baseline (Linux-verified)`.
2. Lấy kết quả lệnh AIO từ Nam. Sửa lỗi cài đặt và selftest trước, rồi cập nhật `requirements.lock` bằng `pip freeze`.
3. Chạy pytest trên Windows thật. Bộ test viết trên Linux có thể lộ khác biệt về đường dẫn, encoding, Qt offscreen.
4. Cùng Nam đi lần lượt mục 5. Nam thao tác và mô tả kết quả, Claude đọc log và sửa. Mỗi lỗi kèm một regression test.
5. Đọc `logs\voicepilot.log` để lấy độ trễ, chạy `tools\benchmark_stt.py` trên 30 câu, cập nhật `docs/BENCHMARKS.md`.
6. Build ZIP, chạy thử trên máy sạch (có mạng và không mạng). Chạy `docs/DEMO.vi.md` ba vòng.
7. Mục giấy phép trong `docs/TODO.md` §2: FLAC GPL-2.0, dữ liệu giọng gốc lessac, so byte LICENSE với bản gốc.

## 7. Câu hỏi còn mở cho Nam

- "tìm giá vàng với" hiện tìm đúng chuỗi "giá vàng với". Có muốn bỏ "với" ở cuối câu tìm kiếm không?
- "mở mới" khi đang ở PowerPoint đang hiểu thành mở tab mới, nên sẽ báo "Chưa có cửa sổ trình duyệt". Có cần lệnh "tạo bài trình chiếu mới" không? PLAN đã xếp việc này vào TODO sau v1.
- Đường dẫn khi mở site giữ nguyên chữ hoa/thường (`github.com/Foo`). Như vậy có ổn không?
- Khi đứng trình chiếu xa máy thì không với tới hotkey. Wake word đang nằm trong TODO sau v1. Có muốn đưa lên sớm hơn không?
