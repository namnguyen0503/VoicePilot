# Handoff — VoicePilot v1 (2026-10-10)

Bàn giao từ Claude Code sang agent tiếp theo (GPT/Codex). Đọc file này trước, sau đó `AGENTS.md`
(quy tắc), `docs/TODO.md` (việc chi tiết), `docs/COMMANDS.vi.md` (lệnh giọng nói). Spec gốc: `docs/PLAN.md`.

## 1. Trạng thái hiện tại

- Đủ 15 chức năng theo `docs/PLAN.md`, kèm overlay, tray, cài đặt, hybrid STT, TTS, đóng gói, tài liệu.
- **Đã chạy trên Windows 10 thật (máy Nam).** 1103 test pass, 2 skip, ruff sạch. Git sạch trên `main`
  (chỉ `models/` untracked, đã bị ignore có chủ đích — không commit).
- Đã xác minh trên máy thật: cài đặt, tải model, `--selftest`, micro thật, Google STT, Gipformer, hotkey
  (với phím tùy chỉnh), parser trên câu nói thật. **Chưa xác minh có hệ thống** các executor: COM PowerPoint,
  SendInput gõ chữ, phím tắt tab, chụp màn hình ở DPI cao, mở app qua `shell:AppsFolder`, PyInstaller build.

## 2. Những gì đã làm trong phiên 2026-10-09/10 (theo commit)

| Commit | Nội dung |
|---|---|
| `775b585` | **Lỗi gốc "vừa lâu vừa sai":** chunk `(frames, 1)` int16 bị chia scale như float → 99% mẫu bị clip. Sau sửa, Google đúng phần lớn, ~0.6–1 s/lệnh. |
| `e0aae9a` | `vad_enabled` (máy Nam tắt VAD; hotkey lần 2 hoặc hết thời lượng tối đa thì dừng ghi). |
| `6a0a73d` | Hotwords cho Gipformer (sherpa-onnx `modified_beam_search`, `modeling_unit="bpe"`, file `bpe.vocab` sinh từ `tokens.txt`). Phrase lấy từ `commands.toml` + bí danh app/web. Lỗi build thì lùi về greedy. |
| `eeafe4d` | `--debug`: file riêng `logs\debug.log` (có transcript) + `logs\clips\turn-*.wav`. Log thường vẫn không chứa transcript. |
| `a8ea71e`, `f2d67b6` | Bí danh claude.ai (gồm cách Gipformer nghe: "cờ lốt", "cờ lao", "cloud chấm ai"…), YouTube Music, Hugging Face, "Microsoft Word/Excel/PowerPoint", "nốt pét". |
| `6a5e333` | Validator của hybrid: kết quả mở web có URL không thuộc bảng `[websites]` (tên miền trần Google đoán) bị coi là không hợp lệ, để Gipformer khớp bí danh được thắng. |
| `2f1ee3a` | "mở X" tìm app đã cài (Shell AppsFolder qua COM, gồm Store app; lỗi COM thì quét `.lnk` Start Menu). Chạy nền, TTL 5 phút, không chặn parse. Thứ tự: website → bí danh `[apps]` → app đã cài. App desktop chạy thẳng exe, Store app chạy `shell:AppsFolder\<AUMID>`. |

## 3. Kiến trúc

```
Hotkey (windows/hotkeys.py) ──signal──▶ Controller (controller.py, main thread Qt)
  1. context_provider.capture()          ← chụp cửa sổ đích TRƯỚC khi hiện overlay
  2. worker: Recorder.record()            → AudioClip 16 kHz mono int16 (audio/capture.py, audio/vad.py)
  3. HybridTranscriber.transcribe(clip, is_valid, cancel)  (stt/hybrid.py; google.py, gipformer.py, hotwords.py)
  4. RuleParser.parse(text, ctx, mode)   → Command  (intent/parser.py + resources/commands.toml;
                                            app_finder = windows/start_menu.py InstalledApps.find)
  5. is_still_target(ctx) → WindowsExecutor.execute(cmd, ctx) → ActionResult
  6. Overlay (ui/overlay.py); TTS (tts/piper.py, chạy qua sherpa_onnx.OfflineTts) chỉ ở chế độ Ra lệnh
```

- Kiểu dữ liệu chung ở `src/voicepilot/contracts.py` (Mode, AudioClip, Transcript, WindowContext, Intent,
  Command, ActionResult…). Khóa `Command.params`: `PPT_SAVE_AS: filename`, `BROWSER_OPEN_SITE: url, site`,
  `BROWSER_SEARCH: query`, `WIN_OPEN_APP: app, app_name`, `DICTATE_TEXT: text`.
- Hybrid: Google chạy trước; Gipformer chạy khi Google lỗi/không hợp lệ/sau 2 s. Kết quả hợp lệ đầu tiên
  thắng; không có kết quả hợp lệ thì lấy kết quả không rỗng tốt nhất, ưu tiên Google. Validator nằm ở
  `controller._validator`.
- `config.py`: `defaults.toml` rồi đè `%LOCALAPPDATA%\VoicePilot\config.toml`; khóa sai thì chỉ khóa đó về mặc định.

## 4. Quyết định đã chốt (chi tiết hơn PLAN)

1. TTS qua `sherpa_onnx.OfflineTts` (giọng Piper `vi_VN-vais1000-medium`) để chỉ có một onnxruntime trong tiến trình.
2. Lệnh PowerPoint chỉ chạy khi cửa sổ phía trước là PowerPoint; lệnh tab cần Edge/Chrome phía trước.
   Mở web, tìm kiếm, mở app, chụp màn hình chạy từ mọi cửa sổ.
3. Chỉ "tiếp theo"/"quay lại" đổi nghĩa theo cửa sổ. Khóa `context` trong `commands.toml` chỉ phá hòa.
4. Parser bỏ lời đệm ("làm ơn", "giúp tôi", "nhé", "ạ"); "đi"/"với" chỉ bỏ ở lệnh ngắn hoặc tên site/app.
5. Tên tệp khi lưu: NFC, chặn ký tự điều khiển và tên thiết bị, ≤240 ký tự, trùng thì thêm " (2)".
6. Hotkey mặc định `Ctrl+Alt+Space` / `Ctrl+Alt+D`, nhưng trên máy Nam bị app khác giữ (lỗi 1409);
   Nam dùng `ctrl+alt+v` / `ctrl+alt+m` trong config. Chưa đổi mặc định.

## 5. Cấu hình máy Nam (không nằm trong repo)

`%LOCALAPPDATA%\VoicePilot\config.toml`: hotkey như trên, `vad_enabled = false`, `stt_mode` hybrid,
và `[websites]` riêng `"cloud.vn"`, `"close.vn"` → `https://claude.ai` (cách Google nghe claude.ai của Nam).

## 6. Vấn đề còn mở

1. **Chưa thử mở app thật bằng giọng nói** sau commit `2f1ee3a` (đã thử parser: "mở roblox player",
   "mở python" ra đúng exe; Notepad ra `shell:AppsFolder\{…}\notepad.exe`). Spotify không cài trên máy.
2. "mở zalo" vẫn mở Zalo web vì `[websites]` có "zalo" và website được xét trước app.
3. Hugging Face bị cả hai engine nghe sai nặng. "Claude dot AI" đọc kiểu tiếng Anh thì Google ra
   "spotify"/"bluetooth" — không cứu được bằng bí danh chung.
4. Hotwords có thể làm lệch chế độ đọc chính tả (chưa đo).
5. Thiếu mẫu "chuyển (sang) slide/tab tiếp theo" trong `commands.toml` (đã đề xuất, Nam chưa duyệt).
6. Nam từng thử lệnh chưa hỗ trợ: tắt wifi/mạng, "tạo file mới trong Excel". Ngoài PLAN — hỏi Nam trước khi làm.
7. Rủi ro Windows chưa kiểm (thứ tự ưu tiên): overlay không lấy focus + SendInput tiếng Việt/emoji
   (Word, Notepad, Edge, Chrome, cửa sổ admin/UIPI); COM PowerPoint (GetActiveObject, chuyển slide khi
   chiếu, SaveAs tên tiếng Việt, timeout 8 s); phím tắt tab Edge/Chrome; chụp màn hình DPI 125–150%;
   PyInstaller `build.ps1` (đường dẫn có dấu, DLL sherpa/PortAudio/pywin32) và `--selftest` trên exe.
8. Benchmark: `tools\benchmark_stt.py` trên 30 câu, cập nhật `docs/BENCHMARKS.md`.
9. Giấy phép: `docs/TODO.md` §2. `.github/workflows/ci.yml` cần kiểm tra có tồn tại.

## 7. Cách debug với Nam

Nam chạy `.\.venv\Scripts\python.exe -m voicepilot --debug`, nói lệnh, rồi bảo "xem log đi". Đọc:
- `%LOCALAPPDATA%\VoicePilot\logs\debug.log` — mỗi lượt: `stt <engine> <ms> text=…`, rồi
  heard/source/app/intent/params/missing.
- `%LOCALAPPDATA%\VoicePilot\logs\clips\turn-*.wav` — audio từng lượt, chạy lại offline qua Google/Gipformer
  để tái hiện.
- `%LOCALAPPDATA%\VoicePilot\logs\voicepilot.log` — độ trễ, trạng thái, mã lỗi.
