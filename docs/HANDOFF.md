# Handoff — VoicePilot v1 (2026-10-10)

Bàn giao VoicePilot. Thứ tự đọc: `AGENTS.md`, tài liệu này, `docs/PLAN.md`, `docs/TODO.md`,
`docs/COMMANDS.vi.md`. Spec gốc: `docs/PLAN.md`. Cập nhật sau đợt kiểm chứng ngày 2026-10-10.

## 1. Trạng thái hiện tại

- Đủ 15 chức năng trong PLAN; Nam chốt hoàn thiện v1, để Wi-Fi và Excel ngoài phạm vi lần này.
- **1168 test pass, 2 skip; ruff sạch trên Windows 10/Python 3.12.** Harness được tạo và chạy trước
  khi sửa app, rồi chạy lại sau sửa. Đủ 15 luồng parser → executor với backend giả lập đều đạt.
- Harness thêm 60 lượt recorder → Controller → hybrid → parser → executor cho 15 chức năng
  qua bốn đường STT. Đã tái hiện/sửa hai lỗi chọn câu slide trong browser và câu tab trong PowerPoint;
  validator chung với benchmark ưu tiên kết quả phù hợp app đích. Đổi foreground chặn mọi tác động.
- Đã chạy suy luận Gipformer và TTS cùng tiến trình, gồm decoder chính tả không hotwords, bằng âm thanh
  tổng hợp. Đây là smoke test, chưa phải số đo giọng nói thật hay chứng cứ executor desktop.
- PyInstaller/PowerShell 5.1 đã tạo ZIP (~178 MB) và chạy exe `--selftest-runtime` trong đường dẫn
  có dấu/khoảng trắng, gồm cả thư mục cấu hình có dấu. ASR/TTS cùng tiến trình và chính tả đều đạt.
  `models/`, `.venv/`, `dist/`, `build/` đều được ignore. CI Windows đã có trong repo, chưa chạy trên GitHub
  vì checkout không có remote.
- **Nam chọn kiểm tra desktop sau.** Lần thử native gặp `LockApp.exe`; harness hiện ghi `blocked`
  khi desktop khóa. Chưa xác nhận có hệ thống focus, SendInput, COM, tab, DPI hay demo đủ 15 chức năng.

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
| `ab8dd82` | Harness trước khi sửa app: 15 luồng giả lập, native probe độc lập, báo cáo trạng thái pass/fail/blocked. |
| `8a8481c` | Thêm cách nói chuyển slide/tab; chính tả dùng decoder greedy riêng không hotwords; nhận tên app bỏ tiền tố Adobe/Microsoft với regression từ lượt "mở Photoshop". Làm rõ mở ứng dụng/trang Zalo. |
| `18357be` | Sửa build Windows: hook webrtcvad-wheels, tránh DLL ICU từ PATH ngoài Python/Windows, UTF-8 trong PowerShell; giữ metadata/giấy phép, pin hash tokens, khôi phục CI. |
| `3d2d790` | `--selftest-runtime` cho exe; benchmark cùng hotwords/validator/chế độ với app; mẫu nhãn 30 câu; harness giữ bằng chứng cũ và báo lỗi timeout. |
| `d88310d` | Sửa espeak thoát tiến trình khi data_dir có dấu: short path, fallback cache ASCII giữ theo lifetime voice; ba regression với fakes. |
| `55b8194` | Sửa BPE/hotwords thoát tiến trình khi config_dir có dấu; helper short path dùng chung và cache; hook riêng loại PocketSphinx, guard build và regression. |
| `aafae05` | Harness CLI downloader thật qua loopback TLS/proxy có auth, CA riêng, redirect/archive/hash/retry/cache; thêm vào CI. |

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
  `controller._validator`, dùng chính sách chung `intent/validation.py` với benchmark. Lệnh slide/tab
  không đúng app đích không chặn một kết quả offline phù hợp; parser vẫn nhận câu để executor báo điều kiện.
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
7. Chính tả offline/hybrid chọn decoder greedy riêng, nạp lười và cache, không dùng hotwords lệnh.
   Lần chính tả offline đầu có thêm thời gian nạp model và sử dụng RAM cho decoder thứ hai.
8. Website vẫn được ưu tiên cho "mở Zalo". Dùng "mở ứng dụng Zalo" hoặc "mở trang Zalo" để chọn rõ đích.

## 5. Cấu hình máy Nam (không nằm trong repo)

`%LOCALAPPDATA%\VoicePilot\config.toml`: hotkey như trên, `vad_enabled = false`, `stt_mode` hybrid,
và `[websites]` riêng `"cloud.vn"`, `"close.vn"` → `https://claude.ai` (cách Google nghe claude.ai của Nam).

## 6. Vấn đề còn mở

Theo thứ tự đề xuất tiếp tục; các kiểm tra desktop chờ Nam chủ động cho chạy lại:

1. **Focus + SendInput** trên desktop mở: Notepad, Word, Edge/Chrome, tiếng Việt/emoji,
   cửa sổ admin/UIPI, đổi foreground trong lúc xử lý. Harness đã có probe Notepad/focus nhưng chưa đạt native.
2. **PowerPoint + browser + screenshot**: GetActiveObject, Save/SaveAs tên tiếng Việt, slideshow,
   timeout COM, phím tab Edge/Chrome, screenshot giấu overlay ở DPI 125–150%. Chạy native harness rồi demo
   đủ 15 chức năng và ba vòng 10 chức năng ổn định nhất theo PLAN.
3. **Mở app thật và tên tiếng Anh**: debug session mới nhất có các lượt Roblox, Visual Studio Code,
   Photoshop, Outlook, Notepad; parser đã nhận nhiều đích. Cần đối chứng app/cửa sổ thực sau khi mở.
   Hugging Face/Claude đọc tiếng Anh vẫn cần clip có câu tham chiếu rõ; không suy đoán alias từ tên app khác.
4. **Benchmark 30 câu thật và chất lượng TTS**: đã có `docs/benchmark-labels.csv` và công cụ đồng bộ
   với app. Chưa có số đo WER/p95/RAM giọng thật; decoder chính tả đã tách hotwords nhưng chưa đo chất lượng
   trên giọng Nam. Kiểm tra thêm câu dài, số, tên riêng, dấu câu khi nghe TTS.
5. **Máy sạch và mạng**: thử ZIP không có Python, offline/online; kiểm proxy/CA tổ chức thực tế nếu có.
   `tools/verify_downloads.py` đã chạy CLI downloader thật qua loopback HTTPS + CONNECT có auth:
   sáu tình huống redirect/archive/CA/hash/retry/cache đạt, không cài CA hay đổi mạng Windows.
   ZIP giải nén sang nơi khác có dấu, PATH chỉ Windows, không biến Python/model ngoài đã đạt ASR/TTS
   và chính tả. 125 module quan sát chỉ đến từ bundle/Windows; thiếu model bị từ chối đúng.
   Harness mới `tools/verify_portable.py`; máy hiện tại vẫn có Python, chưa tính là máy sạch.
6. **Trước phát hành công khai**: quyền phân phối voice fine-tuned từ Lessac còn chưa rõ; xác nhận
   provenance wheel espeak-ng và nguồn tương ứng cho GPL/FLAC. `LICENSE`, cffi, pycparser, header espeak
   và build recipe đã đối chiếu; wheel license texts đã được giữ trong bundle. Điền RepoUrl thật vào SOURCE.txt
   và chạy CI sau khi repo có remote.

Các mục cũ đã xử lý: cách nói chuyển slide/tab; decoder chính tả không hotwords; lựa chọn Zalo rõ đích;
hash tokens; lỗi build; CI thiếu. Wi-Fi/Excel được Nam để ngoài phạm vi v1.

Bằng chứng: `%LOCALAPPDATA%\VoicePilot\verification\20261010-203447-2ab7a648\report.md`;
hướng dẫn tái chạy và giới hạn tại `docs/VERIFICATION.vi.md`. Không tính fake/synthetic/blocked là native desktop pass.
Exe smoke: `build\selftest-679b6f3a17344cba92ab42d27f664b09\logs\selftest.txt`;
bundle files/FLAC: `build\bundle-verification-25330db82b694963bbf9f81588cb10c1\report.json`.
TLS/proxy: `%LOCALAPPDATA%\VoicePilot\verification\downloads-20261010-211026-79eac23c\report.json`.
ZIP cô lập: `%LOCALAPPDATA%\VoicePilot\verification\portable-20261010-211458-d5a9153b\report.json`.

## 7. Cách debug với Nam

Nam chạy `.\.venv\Scripts\python.exe -m voicepilot --debug`, nói lệnh, rồi bảo "xem log đi". Đọc:
- `%LOCALAPPDATA%\VoicePilot\logs\debug.log` — mỗi lượt: `stt <engine> <ms> text=…`, rồi
  heard/source/app/intent/params/missing.
- `%LOCALAPPDATA%\VoicePilot\logs\clips\turn-*.wav` — audio từng lượt, chạy lại offline qua Google/Gipformer
  để tái hiện.
- `%LOCALAPPDATA%\VoicePilot\logs\voicepilot.log` — độ trễ, trạng thái, mã lỗi.

Chỉ phân tích session/lượt mới nhất; giữ mốc đã đọc để không kéo lỗi cũ vào lượt tiếp theo.
Mọi lỗi mới tìm trên Windows thật phải có regression với fakes trước hoặc cùng bản sửa.
