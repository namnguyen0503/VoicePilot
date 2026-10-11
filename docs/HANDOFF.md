# Handoff — VoicePilot v1 (2026-10-11)

Bàn giao VoicePilot. Thứ tự đọc: `AGENTS.md`, tài liệu này, `docs/PLAN.md`, `docs/TODO.md`,
`docs/COMMANDS.vi.md`. Spec gốc: `docs/PLAN.md`. Cập nhật sau kiểm chứng desktop thật ngày 2026-10-11.

## 1. Trạng thái hiện tại

- Đủ 15 chức năng trong PLAN; Nam chốt hoàn thiện v1, để Wi-Fi và Excel ngoài phạm vi lần này.
- **1206 test pass, 2 skip; ruff sạch trên Windows 10/Python 3.12.** Harness được tạo và chạy trước
  khi sửa app, rồi chạy lại sau sửa. Đủ 15 luồng parser → executor với backend giả lập đều đạt.
- Harness thêm 60 lượt recorder → Controller → hybrid → parser → executor cho 15 chức năng
  qua bốn đường STT. Đã tái hiện/sửa hai lỗi chọn câu slide trong browser và câu tab trong PowerPoint;
  validator chung với benchmark ưu tiên kết quả phù hợp app đích. Đổi foreground chặn mọi tác động.
- Harness worker COM tái hiện 25 lỗi chờ/đổi đích trước khi sửa: cả sáu thao tác có thể chạy
  sau timeout hoặc sau khi foreground đổi. Đã thêm deadline, bỏ job hết hạn trong hàng đợi,
  kiểm đích sau lấy app/đọc thuộc tính và trước tác động. 49 tình huống giả lập đạt, gồm
  `Run()` chậm, cửa sổ slideshow mới và shutdown. Shutdown bỏ job chưa bắt đầu, ngăn worker
  mới chồng lên COM cũ bị kẹt; khởi động lại được sau khi COM cũ xong. Không thu hồi COM đã bắt đầu.
- Nút Hủy trước đây chỉ bỏ kết quả: COM đang chờ vẫn lưu sau khi trả về. Harness qua Controller
  tái hiện trước sửa. `Executor.execute(..., cancel=turn.cancel_event)` nay truyền token của từng lượt,
  chặn thao tác chưa bắt đầu và bỏ job đã hủy trong hàng đợi; không ảnh hưởng lượt mới.
- Vòng đời COM: probe tái hiện tham chiếu còn sống sau khi đóng apartment và traceback lỗi
  giữ object sang thread gọi. Worker nay chuyển lỗi thành ActionResult ngay tại chỗ và giải phóng
  app trước CoUninitialize; cả thành công, không có tài liệu và lỗi COM giả lập đều đạt.
- Đã chạy suy luận Gipformer và TTS cùng tiến trình, gồm decoder chính tả không hotwords, bằng âm thanh
  tổng hợp. Đây là smoke test, chưa phải số đo giọng nói thật hay chứng cứ executor desktop.
- Probe TTS native tái hiện callback tiếp tục/hủy bị đảo; fake cũ cũng mô phỏng sai API.
  Đã sửa cả speaker và regression. Player im lặng kiểm đủ câu, PCM và dừng sinh câu sau hủy;
  chưa đánh giá chất lượng nghe.
- PyInstaller/PowerShell 5.1 đã tạo ZIP (~178 MB) và chạy exe `--selftest-runtime` trong đường dẫn
  có dấu/khoảng trắng, gồm cả thư mục cấu hình có dấu. ASR/TTS cùng tiến trình và chính tả đều đạt.
  `models/`, `.venv/`, `dist/`, `build/` đều được ignore. CI Windows đã có trong repo, chưa chạy trên GitHub
  vì checkout không có remote.
- **Nam cho chạy desktop ngày 2026-10-11: đủ 15 chức năng đạt tác động Windows thật.**
  Lượt đi qua Controller/hybrid/parser/executor và overlay, dùng audio/transcript fixture để kiểm
  thực thi. Notepad/Word/Edge/Chrome chèn đúng tiếng Việt và emoji; PowerPoint lưu/lưu tên Việt,
  chuyển slide ở editor/show, bắt đầu/dừng chiếu; tab/web/search ở cả hai browser; screenshot
  PNG 1920×1080 giấu overlay; mở Notepad qua alias và AppsFolder đều đạt.
- Ba vòng 10 chức năng (lưu/slide tiếp/slide trước, sáu lệnh browser, chính tả) đạt; sáu lệnh
  browser lặp riêng trên cả Edge và Chrome. Hotkey thật nhận đúng một WM_HOTKEY; micro thật
  thu 1,02 s thành PCM mono 16 kHz, giữ trong RAM. Chưa tính là demo giọng nói thật.
- Word thật đã tái hiện emoji bị chèn thành `12`. Regression mô phỏng đúng Office trước sửa;
  SendInput nay dùng mã Unicode được chọn + Alt+X cho ký tự ngoài BMP trong Word, rồi thu
  gọn selection để chữ tiếp không ghi đè emoji. BMP và các app khác giữ cách nhập cũ.
- Harness sửa đọc HWND trình chiếu qua Win32 theo PID fixture, MIME HTML, đợi ID tab mới
  và bỏ dialog đăng nhập Edge khỏi số tab. Mỗi lỗi có regression; profile thử tắt implicit signin/sync.
- Tỉ lệ Qt 1,25/1,50 đạt focus/input/screenshot. Đây là ép tỉ lệ app bằng QT_SCALE_FACTOR,
  chưa thay cho đổi DPI toàn Windows hoặc thử nhiều màn hình.

- Tự dừng/VAD: harness WebRTC thật + PCM tiếng Việt tổng hợp bắt lỗi nói ngay
  "lưu"/"mở youtube" bị dùng làm noise floor. Calibration nay nhận biến thiên tiếng nói
  và đếm lại onset trong pre-roll; giữ chặn nhiễu đều/click. Cài đặt có "Tự dừng khi ngừng nói".
  24 tình huống cùng PCM đối chứng: bản cũ lỗi 4, bản sửa đạt 24; sáu bộ PCM bổ sung đạt.
  Recorder lệnh 16 kHz và chính tả từ 48 kHz đều được kiểm, chưa thay cho micro/giọng Nam.
  Một lượt sinh PCM trước đó lệch độ dài clip >100 ms ở chính tả 48 kHz; không còn PCM
  để tái hiện. Harness nay giữ PCM tổng hợp và hỗ trợ --reuse; chưa kết luận biến thiên đó
  là lỗi recorder. Giọng nhỏ/ngắt câu trên micro thật vẫn cần đối chứng.
  Báo cáo đối chứng: `verification\vad-original-replay-ddbfd758a0794590aea87b14e8dc0cb6`
  và `verification\vad-fixed-replay-68832d399a68498686e88fd14c4fa58c` trong LocalAppData.

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

`%LOCALAPPDATA%\VoicePilot\config.toml`: hotkey như trên, `vad_enabled = true` (đã bật theo yêu cầu tự dừng ngày 2026-10-11), `stt_mode` hybrid,
và `[websites]` riêng `"cloud.vn"`, `"close.vn"` → `https://claude.ai` (cách Google nghe claude.ai của Nam).

## 6. Vấn đề còn mở

Phần code 15 chức năng và kiểm chứng desktop bằng câu điều khiển đã hoàn thành. Các điều kiện
sau cần dữ liệu giọng nói hoặc môi trường riêng; không đánh dấu đạt bằng fake/synthetic:

1. **Giọng nói thật:** thu 30 câu theo `docs/benchmark-labels.csv`, chạy Google/offline/hybrid,
   ghi WER, intent/params accuracy, p50/p95, RAM; đo từ ngừng nói đến phản hồi và quay demo 15
   chức năng, ba vòng 10 chức năng theo PLAN. Nghe TTS câu dài/số/tên/dấu câu. Smoke native
   micro và suy luận tổng hợp đã đạt, chưa chứng minh p95 4 giây hay chất lượng giọng Nam.
2. **Điều kiện Windows khác:** UIPI/cửa sổ admin, DPI hệ thống 125–150% và nhiều màn hình;
   PowerPoint nhiều tài liệu, tệp chưa lưu/trùng tên và COM bị treo trên Office thật. Fakes đã
   kiểm các nhánh timeout/cancel/boundary; desktop hiện xác nhận tài liệu thử với ba slide.
3. **Tên app/web tiếng Anh:** đối chứng các app riêng như Roblox/VS Code/Photoshop/Outlook
   và clip có câu tham chiếu Hugging Face/Claude. Notepad alias/AppsFolder đã mở cửa sổ thật;
   không suy đoán alias hoặc chất lượng nhận dạng từ tên app khác.
4. **Máy sạch/mạng/CI:** chạy ZIP trên Windows không Python, có/không mạng; proxy/CA tổ chức
   nếu có. ZIP cô lập và TLS loopback đã đạt trước đó. CI có trong repo nhưng checkout chưa
   có remote; không có GitHub run. Không tiếp tục kiểm checksum theo yêu cầu Nam.
5. **Trước phát hành công khai:** quyền phân phối voice fine-tuned từ Lessac và provenance
   wheel espeak-ng/nguồn tương ứng GPL/FLAC vẫn cần xác nhận; xem `THIRD_PARTY_NOTICES.md`.
   Điền RepoUrl thật vào SOURCE.txt sau khi có remote. ZIP tại máy là artifact kiểm chứng.

Bằng chứng desktop: `%LOCALAPPDATA%\VoicePilot\verification\20261011-010358-974613c8\report.md`
(215 kiểm tra đạt: 128 fake, 79 Windows thật, 5 runtime, pytest/ruff/selftest).
1200 pytest pass, 2 skip; ruff sạch. Trước sửa Word: `verification\word-observe-bf05a06e29724ca48f0e3fe099bfaae4\report.md`
ghi expected/actual emoji thành `12`; regression giữ hành vi đó bằng fakes. Các báo cáo Qt scale bắt đầu `qt-scale-1.25-`
và `qt-scale-1.5-`, mỗi báo cáo 12 native checks. Hướng dẫn/giới hạn: `docs/VERIFICATION.vi.md`.
Bằng chứng worker COM/cancel/vòng đời trước đây: `verification\20261010-223811-4c8f9cca\report.md`.
Không tính câu STT fixture hoặc âm thanh tổng hợp thành benchmark/demo giọng thật.

## 7. Cách debug với Nam

Nam chạy `.\.venv\Scripts\python.exe -m voicepilot --debug`, nói lệnh, rồi bảo "xem log đi". Đọc:
- `%LOCALAPPDATA%\VoicePilot\logs\debug.log` — mỗi lượt: `stt <engine> <ms> text=…`, rồi
  heard/source/app/intent/params/missing.
- `%LOCALAPPDATA%\VoicePilot\logs\clips\turn-*.wav` — audio từng lượt, chạy lại offline qua Google/Gipformer
  để tái hiện.
- `%LOCALAPPDATA%\VoicePilot\logs\voicepilot.log` — độ trễ, trạng thái, mã lỗi.

Chỉ phân tích session/lượt mới nhất; giữ mốc đã đọc để không kéo lỗi cũ vào lượt tiếp theo.
Mọi lỗi mới tìm trên Windows thật phải có regression với fakes trước hoặc cùng bản sửa.
