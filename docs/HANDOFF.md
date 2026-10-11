# Handoff — VoicePilot v1 (2026-10-12)

Bàn giao VoicePilot. Thứ tự đọc: `AGENTS.md`, tài liệu này, `docs/PLAN.md`, `docs/TODO.md`,
`docs/COMMANDS.vi.md`. Spec gốc: `docs/PLAN.md`. Cập nhật sau kiểm chứng desktop thật ngày 2026-10-11.

## 1. Trạng thái hiện tại

- Đủ 15 chức năng gốc trong PLAN cùng các bổ sung Nam đã yêu cầu. Ngày 2026-10-12 Nam thêm
  tạo tài liệu trắng Word/Excel/PowerPoint, cửa sổ mới/ẩn danh cho browser và đóng cửa sổ;
  Wi-Fi vẫn ngoài phạm vi.
- **1702 test pass, 2 skip; ruff sạch trên Windows 10/Python 3.12.** Harness được tạo và chạy trước
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

- Tìm kiếm nhận câu hỏi tự nhiên theo yêu cầu Nam: "giá vàng hôm nay bao nhiêu",
  "Python là gì", "thời tiết Hà Nội hôm nay", các mẫu hỏi giá/lý do/cách làm.
  `{question}` trong commands.toml chỉ dành cho BROWSER_SEARCH, chạy sau lệnh rõ;
  query giữ cả từ hỏi, bỏ lời đệm. Chính tả vẫn chỉ chèn text. Không lấy/đọc giá vàng.
  Regression tái hiện parser gốc và Controller ở Google/offline/hybrid/fallback: 0/8 trước,
  8/8 sau. Edge/Chrome thật đều mở đúng một tab Google với cả query tiếng Việt;
  selftest của exe cũng kiểm câu báo lỗi. Báo cáo trong LocalAppData:
  `verification\natural-search-3e313a0a0a67439083f44d7dc5acd2d0\before` và `after`,
  `verification\search-question-03d1fd672a0640a9b8e67624c83bdd9b\edge` và `chrome`.

- Nam yêu cầu thêm lệnh custom ngày 2026-10-11. Cài đặt có tab quản lý
  thêm/sửa/xóa/nhân bản/bật-tắt, nhập/xuất JSON v1, thử câu nói không thực thi.
  Nhiều mẫu gọi, tối đa 3 tham số động, 15 hành động v1 + WIN_HOTKEY;
  giới hạn theo nhóm cửa sổ/tên exe. Custom ưu tiên trước built-in; hòa báo xung đột.
  Chính tả không chạy custom. Đường dẫn app/phím cố định; URL mã hóa captures.
  Parser/config được giữ theo lượt, cập nhật vào lượt tiếp theo; hotwords nạp lại khi cần.
  Harness trước code báo thiếu module; sau triển khai có 64 lượt STT/Controller cho
  16 hành động. Native 59 checks đạt (gồm 7 custom): Notepad chữ ký Unicode/Ctrl+A/
  chính tả, search/URL template ở Edge và Chrome. UI checkbox khó đọc trên Windows
  đã có regression tương phản trước sửa. Báo cáo:
  `verification\custom-ff9df862356245b1947c3b819b15555f\report.md` trong LocalAppData.
  Harness: `tools/verify_custom_commands.py`; bộ ví dụ: `docs/custom-commands.example.json`.
  Đây là mẫu do người dùng tự tạo, chưa bổ sung LLM/NLU hay macro nhiều hành động.

- Cài đặt không hiện khi launcher dùng Windows STARTUPINFO `SW_HIDE`: Qt báo visible
  nhưng HWND vẫn ẩn. Harness pythonw thật tái hiện trước sửa và xác nhận sau sửa.
  Mở Cài đặt nay kiểm native visibility, hiện lại HWND và phục hồi cửa sổ thu nhỏ;
  chỉ áp dụng khi người dùng yêu cầu Cài đặt, không tác động overlay. Hai regression
  với fakes bắt lỗi trước sửa và đạt sau sửa. App tương tác phải khởi chạy Normal.

- Bổ sung theo yêu cầu Nam: hai tab Voice/Text, ô nhập Enter/nút gửi, micro bị chặn
  khi Text; cùng pipeline lệnh/custom/chính tả và trả focus về đích trước gửi.
  Mở lại tab có ba chính sách Voice mặc định/nhớ lựa chọn/luôn Text.
  Đổi Ra lệnh/Chính tả trong capture giữ PCM và cập nhật giới hạn thời lượng;
  khi đã nhận dạng/thực thi thì hủy lượt cũ.
- Hướng dẫn dùng trong cửa sổ riêng có danh mục/tìm kiếm/phím tắt theo cấu hình,
  resource dự phòng được đóng gói cùng app.
- Chín lệnh mới và custom action tương ứng: Power saver (power plan theo Nam chốt),
  tăng/giảm độ sáng và âm lượng, Play/Pause/Next/Previous qua Windows SMTC.
  Native đạt trên Lenovo: power plan bật/khôi phục; volume 50→52→50%; sáng 100→89→100%;
  bốn API media và callback tương ứng. Power plan/âm lượng/độ sáng được khôi phục.
- Harness trước code: Voice/Text 14 lỗi, hệ thống 29 lỗi. Lỗi WMI native đã có regression
  dispatch/ExecMethod/readback thiếu và chậm/level không liên tục. WinRT khởi tạo
  MTA hoặc STA có sẵn, cân bằng uninitialize. Không gửi phím toggle thay cho Play/Pause.
- Ảnh Nam gửi tái hiện crash pytest sau khi báo pass; Event Log xác nhận pyside6.abi3.dll.
  App nay có owner QApplication và chủ động giải phóng các cửa sổ/đối tượng khi shutdown;
  regression giải phóng cửa sổ/menu trước teardown. Đối chứng tiếp tìm đúng hai overlay
  còn sống trong harness execute(); nay controller/overlay được giải phóng và regression
  kiểm không còn cửa sổ thừa sau lượt. Pytest dọn Qt trước khi interpreter đóng;
  bộ test đầy đủ 1496 pass/2 skip đã thoát mã 0 sau sửa.
- Native Text: Notepad giữ đúng tiếng Việt/emoji, Enter gửi một lần, mở lại giữ đích,
  micro không mở, về Voice trả focus, composer nằm trọn màn hình. Tệp fixture/PNG/báo cáo
  trong build/input-native-final-dc7a327dc24d48818c90ad7add38a5a3; hệ thống ở build/system-native-final.json; harness và giới hạn ở docs/VERIFICATION.vi.md.

## 2. Những gì đã làm trong phiên 2026-10-09/10 (theo commit)

Bổ sung 2026-10-12: tạo tài liệu trắng ở Word/Excel/PowerPoint, cả Home và editor.
Home chọn đúng template Blank qua MSAA trên HWND đã chụp; editor gửi Ctrl+N. Không dùng
ROT để tránh nhầm instance Office; accessibility objects sống/được giải phóng trong worker COM.
Worker giữ guard focus/cancel/deadline/shutdown; lỗi probe không gửi phím dự phòng bừa.
Câu "tạo file mới" chọn theo ngữ cảnh Word/Excel/PowerPoint; câu rõ ở app khác báo điều kiện.
Thêm cửa sổ thường Ctrl+N và ẩn danh Ctrl+Shift+N ở Edge/Chrome; tab mới giữ nguyên.
Năm hành động có custom/prompt mặc định/hotwords/selftest/manual và bốn đường STT.
Custom "Ứng dụng khác" cũ tiếp tục khớp Word/Excel để giữ cấu hình đã có.

Nam bổ sung đóng cửa sổ: Alt+F4, chặn Desktop/taskbar bằng HWND shell/desktop,
ancestor và lớp Win32. Explorer folder vẫn đóng được; lỗi đọc native chặn tác động.
Kiểm lại focus/cancel sau probe. Không tự trả lời hộp hỏi lưu của ứng dụng.
Harness trước code 36 fail; sau code 37 pass, gồm bốn đường STT. Native Notepad
đóng tài liệu đã lưu, giữ tài liệu chưa lưu với hộp hỏi lưu; năm HWND Desktop/taskbar
thật bị chặn (keys fake, không gửi Alt+F4 lên Desktop). Evidence:
`build/close-window-native-f4e67ff991ee4248889b34b67dc1fb7e/report.json`.

Harness `tools/verify_new_documents.py`: trước code 50 fail/1 pass; sau code 77 pass,
gồm 20 lượt Controller/hybrid/parser/executor. Native `tools/verify_new_documents_native.py`
đạt Home/editor ba Office, tab/window/private ở Edge/Chrome, kiểm đúng một tài liệu/cửa sổ,
trắng/chưa lưu và giữ nội dung fixture cũ. Evidence:
`build/new-documents-native-60e3e8e85b0046fbaf4ab37ad463919f/report.json`.
Probe ban đầu dùng IDispatch của child cho AccessibleChildren gây access violation;
bản adapter QueryInterface IAccessible trước traversal, VariantClear/release có regression.
Harness FileTab MSO không phải button; dùng Alt+F để vào Home. Tài liệu/profile của Nam
không được dùng làm fixture. Đây là kiểm tác động với STT fixture, chưa là benchmark giọng thật.

Bổ sung theo yêu cầu Nam: `tắt tiết kiệm pin` khôi phục power plan trước khi app bật
Power saver. Lưu trạng thái riêng `power-state.json` trước tác động; bật lặp không
ghi đè, tắt sau mở lại app vẫn khôi phục, lỗi khôi phục giữ dữ liệu. Nếu người dùng
tự đổi plan khác thì giữ lựa chọn đó. Power saver bật từ phiên cũ/bên ngoài mà chưa
lưu plan trước đó báo thiếu dữ liệu, không đoán Balanced hay plan khác.

Cài đặt có tab **Prompt mặc định** để sửa cách gọi mọi lệnh có sẵn; parser/hotwords
cùng nhận override ở lượt kế tiếp, custom vẫn ưu tiên. Hướng dẫn trong app có mục
Prompt hiện tại. Tab **Khôi phục** có ba mức: prompt (giữ custom/setting khác), toàn
bộ setting (giữ custom), factory (xóa mọi dữ liệu riêng của app, gồm custom/log/clip/
cache). Hai mức đầu là bản nháp chờ Lưu; giữ cả custom đang sửa. Factory xác nhận
riêng, hủy lượt, đóng app và chờ tiến trình cũ thoát trước xóa/mở log, rồi mở lại với
cài đặt gốc. Khôi phục power plan đã lưu trước khi xóa trạng thái; không xóa binaries,
models hay Pictures/VoicePilot. Chặn reset nhầm thư mục dùng chung/cài đặt/liên kết.

Harness `tools/verify_restore_features.py` tạo trước code: 23 fail trước triển khai,
36 pass sau. `tools/verify_restore_native.py` kiểm PowrProf thật, bật lặp/khôi phục
qua manager mới, factory trên dữ liệu thử với đóng/mở hai tiến trình và Qt Windows.
Đã tái hiện nhãn đen trên nền tối ở hai tab mới; regression fail trước sửa, đạt sau.
Bằng chứng native: `build/restore-native-8817bc03308d406cadd06d3e7b7e77bd`.
TTS bật lại được khi app khởi đầu tắt TTS; reset history limit/overlay position cũng
có kiểm chứng. Dữ liệu đang dùng của Nam không bị reset trong harness.

Cập nhật tự dừng ngày 2026-10-11: Nam báo nền ồn làm không dừng. Harness mới tạo trước
code tái hiện 31/80 lỗi; mở rộng đối chứng 96 checks: cũ 49, mới 86 đạt. Recorder nay dùng
Silero VAD v6.0 offline qua sherpa-onnx (một CPU thread, model khoảng 2,3 MB); reset trạng thái
giữa lượt, giữ nguyên PCM cho STT, giữ điều kiện VAD tắt/hủy/đổi mode. Sau khoảng nghỉ, cần
90 ms tiếng nói được xác nhận mới reset bộ đếm, để nhiễu ngắn không kéo dài lượt mãi.
Model/giấy phép được thêm vào manifest/downloader/bundle, không làm checksum theo yêu cầu Nam.
WebRTC là fallback nếu thiếu/không nạp được model. Selftest runtime kiểm VAD cùng ASR/TTS.

Không còn lượt đã nhận tiếng nói bị giữ mở trong bộ thử; 16/16 noise-only không tạo clip.
Median/p95 endpoint 951/1350 ms; max 1467 ms. Hai checks trượt mục tiêu 1,4 s và tám checks
SNR 0 dB mất đầu/bỏ sót giọng; không coi các điều kiện này đạt. TV/tiếng người khác chưa
được tách người nói. Chi tiết ở docs/VERIFICATION.vi.md và build/noisy-endpoint-release.
Nam cho thu micro thật 20 s trong nền ồn: build/live-endpoint-1 giữ WAV/báo cáo riêng tư.
Silero tự dừng các lượt hoàn chỉnh; offline đọc được hai câu mở YouTube/chụp màn hình.
WebRTC nhận tiếng bíp đầu bản thu là lượt nói, Silero bỏ qua. Lượt cuối bị giới hạn bởi
20 s thu thử, chưa dùng để kết luận điểm dừng. Lỗi cue tone có regression với fakes.
Đây chưa phải benchmark 30 câu đã gán nhãn.

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
