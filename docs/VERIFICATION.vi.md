# Harness kiểm chứng VoicePilot

Chạy harness trước khi sửa app để giữ kết quả nền, rồi chạy lại sau sửa:

```powershell
.\.venv\Scripts\python.exe tools\verify_features.py --checks --runtime
```

Không có `--windows`: kiểm tra parser → executor cho đủ 15 chức năng và thêm
60 lượt qua recorder thật, Controller, HybridTranscriber, parser và executor. Nguồn thu
là callback PCM 48 kHz int16 giả lập; văn bản STT và tác động Windows được kiểm soát.
Bốn đường xử lý gồm Google, offline, hybrid và Google lỗi → offline. Mỗi lượt phải có
đúng một tác động, giữ tham số Unicode và phản hồi đúng chế độ; chính tả không phát TTS.
Bốn tình huống thêm kiểm chọn kết quả đúng app khi Google nhầm slide/tab, đổi foreground
và hủy trong lúc worker PowerPoint đang chờ COM. Sau hủy phải không lưu và không có phản hồi muộn.
Các lượt này có nhãn `fake_pipeline`, không chứng minh độ chính xác ASR hay thao tác desktop.

Nhóm `fake_com_worker` chạy worker PowerPoint thật qua executor mặc định, với COM giả lập.
49 tình huống kiểm đủ sáu thao tác khi bình thường, timeout/hủy lúc lấy app/đọc thuộc tính,
đổi foreground trong lúc chờ, lệnh hết hạn trong hàng đợi và bước tiếp sau `Run()` chậm.
Hết hạn hoặc đổi đích trước thao tác phải không gây tác động. Lời gọi COM đã bắt đầu
không thể thu hồi; worker chỉ chặn các bước còn chưa bắt đầu. Cửa sổ trình chiếu do `Run()`
tạo ra có thể tự lấy focus nên bước chuyển về slide 1 chỉ kiểm hạn/hủy, không so HWND cũ.
Probe shutdown bỏ việc còn chờ, giữ một apartment khi COM cũ kẹt và khởi động lại sau khi
worker cũ kết thúc. Test cố tình bỏ guard deadline xác nhận harness vẫn phát hiện lưu muộn.
`Executor.execute` nhận `cancel` dạng Event tùy chọn; Controller truyền Event riêng của lượt,
WindowsExecutor kiểm trước tác động và PowerPoint worker kiểm lại sau mỗi bước COM chuẩn bị.
Ba probe vòng đời theo dõi thread và thứ tự giải phóng object trước CoUninitialize ở nhánh
thành công, không có tài liệu và lỗi Save. Future chỉ truyền ActionResult, không giữ traceback COM.

`--checks` chạy thêm pytest, ruff và selftest. Pytest dùng thư mục
tạm mới cho mỗi lần chạy, tránh quyền truy cập/cache cũ của lần chạy trước.

Thêm `--runtime` để kiểm tra nạp ASR/TTS trong cùng tiến trình và nhận dạng
âm thanh tổng hợp qua decoder lệnh và decoder chính tả không có hotwords.
Chế độ này chạy được khi desktop khóa; kết quả là smoke test, không phải benchmark
giọng thật. `--windows` ghi `blocked` và không mở cửa sổ khi máy đang khóa.

Probe runtime dùng PiperSpeaker thật với player chỉ thu PCM, không phát loa. Nó kiểm ba
câu phản hồi có số/tên/dấu câu và hủy sau callback đầu: callback sherpa trả 1 để tiếp tục,
0 để dừng; khi hủy không sinh câu tiếp theo. Chất lượng nghe vẫn cần Nam kiểm riêng.
Quy ước callback theo [ví dụ Python của sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/python-api-examples/offline-tts-play.py).

Với `--windows`, harness mở Notepad, Word, PowerPoint và profile thử riêng cho Edge/Chrome.
Nó tạo tài liệu mới để thử, đóng những cửa sổ nó đã tạo sau khi xong. Không dùng
tài liệu đang làm hoặc profile trình duyệt cá nhân. Trong lúc chạy không thao tác
bàn phím/chuột để kết quả focus có thể lặp lại.

Báo cáo JSON/Markdown và đầu ra công cụ nằm dưới
`%LOCALAPPDATA%\VoicePilot\verification\<thời điểm>-<id>\`.
Dùng `--output <thư mục mới>` để chọn nơi lưu. Kết quả `blocked` nghĩa là thiếu
điều kiện kiểm thử, không được tính là đạt. Exit code khác 0 nếu có fail hoặc blocked.
Harness từ chối thư mục báo cáo đã tồn tại để giữ bằng chứng cũ.

Bản portable có thể kiểm tra suy luận native mà không cần mở desktop:

```powershell
.\dist\VoicePilot\VoicePilot.exe --selftest-runtime
```

Đọc `%LOCALAPPDATA%\VoicePilot\logs\selftest.txt` để xem kết quả. Cờ `--selftest`
vẫn chỉ kiểm tra nhanh file/import; `--selftest-runtime` nạp model, tổng hợp câu,
nhận dạng lại và kiểm intent, sau đó thử decoder chính tả. Không thu micro, không
phát âm thanh. `build.ps1` dùng phép thử này và giữ báo cáo trong `build\selftest-<id>`.

Harness quan sát chữ tiếng Việt/emoji trong Notepad; focus trước/sau khi overlay
hiện; chặn đích đã đổi; PNG và kích thước màn hình; cửa sổ app sau khi mở;
tệp PowerPoint, trạng thái đã lưu, slide và trình chiếu; số tab/URL và tiêu đề
tab trên profile riêng của từng trình duyệt. Mở web/tìm kiếm dùng ShellExecute thật
với tham số profile thử riêng. Các kiểm tra backend giả lập được ghi nhãn `fake`.

Harness có smoke micro thật (PCM chỉ trong RAM), hotkey thật và chính tả Word. Nó chưa
thay cho đo giọng nói thật, chất lượng giọng đọc, cửa sổ admin/UIPI,
DPI 125–150% hoặc máy sạch không Python. Những điều kiện đó cần chạy riêng và ghi
bằng chứng cụ thể. Dữ liệu tổng hợp không được tính là benchmark giọng nói của Nam.

Kiểm tra downloader qua TLS và proxy cục bộ, chạy được khi desktop khóa:

```powershell
.\.venv\Scripts\python.exe tools\verify_downloads.py
```

Harness gọi CLI downloader thật trong tiến trình con với `HTTPS_PROXY` có Basic auth và
`SSL_CERT_FILE` riêng. Nó kiểm redirect, hash, giải nén archive, từ chối CA không tin cậy,
giữ file cũ khi lỗi, tải lại và bỏ qua file đã đúng hash. File `.part` phải được dọn sau lỗi.
Mỗi lần có thư mục báo cáo mới, gồm `report.json` và stdout/stderr từng tình huống.

Máy chủ/proxy chỉ nghe và kết nối loopback; payload là fixture, không phải model thật.
CA chỉ được tin trong tiến trình con, không cài vào kho chứng thư Windows và không đổi
biến môi trường của app. Phép thử này xác nhận cơ chế proxy/CA trên Windows; vẫn cần thử
proxy/CA tổ chức thực tế nếu có, cùng endpoint model công khai và máy sạch.

Kiểm tra ZIP sau khi chuyển sang nơi khác:

```powershell
.\.venv\Scripts\python.exe tools\verify_portable.py --zip dist\VoicePilot-0.1.0-win64.zip
```

Harness kiểm CRC rồi giải nén ZIP vào thư mục mới có dấu/khoảng trắng. Exe chạy từ thư mục
báo cáo với PATH chỉ chứa Windows/System32 và Windows; bỏ biến Python/venv/model từ môi
trường ngoài, dùng config mới có dấu. Nó yêu cầu bằng chứng suy luận ASR/TTS/chính tả thực,
quan sát DLL lúc tiến trình chạy và từ chối DLL nằm ngoài bundle hoặc Windows. Phép thử
âm tính dùng thư mục model không tồn tại phải trả mã lỗi và báo thiếu model.

`report.json` giữ SHA-256 ZIP; mỗi tình huống giữ selftest, stdout/stderr và danh sách module
đã nạp. Bản giải nén được giữ để đối chiếu. Đây là kiểm tra cô lập trên máy hiện tại: Python
vẫn được cài trên máy, nên chưa thay cho thử trên máy sạch, Office hay micro thật.

## Kết quả desktop ngày 2026-10-11

`--windows --checks --runtime` đạt **215 checks**, gồm 128 fake, 79 desktop native,
5 runtime và 3 công cụ. Pytest 1200 pass/2 skip, ruff sạch, selftest đạt.
Báo cáo: `%LOCALAPPDATA%\VoicePilot\verification\20261011-010358-974613c8\report.md`.

Các lượt native nay đi qua Controller → HybridTranscriber → parser → WindowsExecutor
và overlay thật; recorder/transcript fixture và speaker im lặng để tách lỗi thực thi khỏi ASR.
Quan sát nội dung/slide/tệp/tab/PNG thực, không chỉ status OK. Website/search vẫn dùng
ShellExecute thật nhưng thêm tham số profile thử. Mỗi lần phải quan sát ID tab mới;
Edge sync-confirmation dialog không phải tab và bị loại khỏi đếm. Mỗi vòng reset hai tab gốc.

10 chức năng lặp ba vòng: PowerPoint save/next/previous, sáu browser intent, chính tả
Notepad. Sáu browser intent còn được lặp riêng trên Chrome. Word/Notepad/Edge/Chrome
đạt tiếng Việt + emoji; Word trước sửa chèn emoji thành `12`, regression fake tái hiện
trước sửa. Alt+X được giới hạn cho ký tự ngoài BMP trong Word và thu gọn selection sau chuyển.

Hotkey dùng Ctrl+Shift+F24 riêng, unregister khi xong; không đổi phím tắt cấu hình của Nam.
Micro thu khoảng 1 s, không lưu clip/transcript. Các probe keys ở QT_SCALE_FACTOR=1.25/1.5
đạt 12 checks mỗi probe; DPR lần lượt 1,25/1,50, PNG vẫn 1920×1080 và overlay được phục hồi.
Đây là tỉ lệ Qt ép riêng cho tiến trình, chưa thay đổi DPI của hệ thống hay kiểm nhiều màn hình.

Các giới hạn còn lại ở HANDOFF §6/TODO: benchmark 30 câu và demo giọng thật, nghe TTS,
UIPI/admin, các điều kiện Office/DPI khác, máy sạch và CI sau khi có remote. Không đánh dấu
chúng đạt bằng transcript fixture hoặc âm thanh tổng hợp.


## Kiểm chứng tự dừng khi ngừng nói

Chạy `.\.venv\Scripts\python.exe tools/verify_endpoint.py` trước/sau sửa VAD.
Harness dùng WebRTC thật và bốn câu tiếng Việt tổng hợp, kiểm nói ngay, nói nhỏ, nhiễu nhẹ,
nghỉ 300 ms giữa câu, không cắt âm đầu và timeout im lặng. Recorder thật nhận callback PCM
fixture ở 16/48 kHz, kiểm cả Ra lệnh và Đọc chính tả; không phát loa/thu micro/chạy lệnh.
Mỗi báo cáo giữ `synthetic-*.npy` (PCM tổng hợp, không phải giọng người dùng).
Đối chứng cùng dữ liệu bằng `--reuse <thư mục báo cáo> --label vad-replay`.
Kết quả này không chứng minh chất lượng trên micro hoặc giọng Nam; tiếng nhỏ có thể bị VAD
coi là im lặng sớm, cần tăng khoảng dừng trong Cài đặt nếu nói ngắt quãng.


## Harness lệnh tùy chỉnh

`python tools/verify_custom_commands.py` chạy acceptance test riêng với fakes/Qt,
được tạo trước tính năng. Kiểm CRUD, import/export/persistence, ngữ cảnh/xung đột,
16 hành động ở Google/offline/hybrid/fallback và tham số giữ Unicode.
`--windows` dùng Notepad fixture và profile riêng Edge/Chrome; báo cáo trong
LocalAppData/VoicePilot/verification/custom-*. Transcript vẫn là fixture;
không tính thành demo/benchmark giọng nói. Báo cáo ngày 2026-10-11 có 59 checks đạt,
trong đó 7 checks custom; không dùng hoặc đóng tài liệu/profile riêng của Nam.


## Harness Voice/Text và điều khiển Windows

- `python tools/verify_input_modes.py`: acceptance đã tạo trước code (14 lỗi ban đầu),
  giữ PCM khi đổi mode, hủy STT cũ, không mở micro trong Text, chống gửi lặp,
  kiểm đích, cấu hình ba chính sách và cửa sổ hướng dẫn.
- `python tools/verify_input_native.py --windows --report-dir build/input-native-<ten-moi>`:
  Qt Windows, Notepad/tệp riêng, Enter chèn Unicode/emoji đúng một lần, focus về
  đích, mở lại Text, chuyển Voice, vùng màn hình và hướng dẫn. Báo cáo/PNG được giữ
  trong thư mục mới. Audio/STT dùng fixture; không chứng minh chất lượng giọng nói.
- `python tools/verify_system_native.py --windows --report build/system-native.json`:
  tác động thật chín lệnh mới, giữ/khôi phục power plan, độ sáng và âm lượng.
  Media dùng MediaPlayer SMTC fixture riêng (cần cài `winrt-Windows.Media.Playback==3.2.1`
  để chạy harness; package này không phải dependency app).
  Harness kiểm API nhận cả Pause/Play/Next/Previous và bốn callback tương ứng.
  Không chạy khi Windows đang khóa; để máy không thao tác trong lúc chạy.
- Lỗi native mới có regression: WMI dispatch/ExecMethod/thiếu output, level driver,
  readback chậm, apartment STA/MTA, QObject/Qt khi thoát, vị trí composer ngoài màn hình.
  Chạy đầy đủ pytest với mã thoát tiến trình 0; dòng “passed” chưa đủ nếu Python crash sau đó.

## Tự dừng trong môi trường ồn

`python tools/verify_noisy_endpoint.py --speech-dir <thu-muc-PCM-synthetic>
--output build/<ten-moi>` chạy Recorder thật, gồm resample 48 kHz, với PCM tiếng Việt
được giữ từ `tools/verify_endpoint.py`. Không dùng micro, STT, playback hay thao tác desktop.
Harness tạo trước code, giữ PCM nhiễu và báo cáo trước/sau trong thư mục riêng.

96 checks: bốn câu, hai chế độ/rate, tiếng quạt/broadband ở SNR 6/0 dB, tiếng ồn tăng
sau câu nói, tiếng máy dao động, gõ phím, nói ngay, giọng nhỏ, nghỉ 300 ms và chỉ có nhiễu.
Điều kiện chặt: không mất đầu câu quá 100 ms, endpoint trong 1,4 s sau cuối tiếng nói;
chỉ có nhiễu phải timeout ở khoảng 5 s mà không tạo clip.

Đối chứng 2026-10-11: bản WebRTC/RMS cũ đạt 49/96; Silero v6 đạt 86/96.
Tất cả lượt đã nhận tiếng nói đều tự kết thúc, 16/16 lượt chỉ có nhiễu không tạo clip.
Median/p95 thời gian endpoint là 951/1350 ms; tối đa 1467 ms trên các lượt đã nhận tiếng nói.
10 checks chưa đạt: hai lượt nhiễu tăng mạnh chậm hơn mục tiêu 1,4 s; tám lượt tiếng quạt
ở SNR 0 dB bỏ sót tiếng nói hoặc mất đầu câu. Đây là giới hạn đã đo, không đánh dấu pass.
Nguồn báo cáo: `build/noisy-endpoint-baseline-expanded` và `build/noisy-endpoint-release`.
Âm thanh tổng hợp không thay benchmark giọng Nam/micro thật. TV/người khác nói chưa
được tách người nói; không khẳng định chống được mọi tiếng ồn.

Nam cho thu 20 s từ Realtek ngày 2026-10-11: `build/live-endpoint-1/microphone.wav` và
`report.json` giữ cục bộ, không commit. `tools/record_endpoint_probe.py` thu khi người dùng
đã đồng ý và có tiếng bíp; `--reuse <wav>` chỉ đối chứng lại. Silero kết thúc bằng silence
ở các lượt hoàn chỉnh; offline đọc được mở YouTube và chụp màn hình. Tiếng bíp đầu clip
bị WebRTC nhận là lượt nói, Silero bỏ qua. Lượt cuối còn đang nói khi hết 20 s không có
nhãn kết thúc; không đánh dấu lỗi/đạt endpoint cho lượt này. Đây chưa phải benchmark WER.

## Prompt và khôi phục

Chạy `python tools/verify_restore_features.py`: pytest acceptance được viết/chạy trước
implementation (23 fail), sau triển khai 36 checks đạt. Toàn bộ fixture nằm trong
`build/restore-harness-*`, không đọc/xóa dữ liệu đang dùng. Kiểm persistence, parser,
hotwords, snapshot lượt, giữ custom qua hai mức reset, custom đang sửa chỉ lưu khi
nhấn Lưu, factory confirmation, lỗi power/state, path guard và privacy.

`python tools/verify_restore_native.py` cần Windows. PowrProf bật Power saver lặp,
manager mới tắt/khôi phục, cuối probe trả về plan lúc bắt đầu. App nguồn thật chạy
trong thư mục dữ liệu riêng có custom và fixture private; signal factory đóng app,
child chờ parent thoát, xóa dữ liệu rồi mở lại với config gốc. Kiểm log/fixture cũ bị
xóa, default hotkeys/custom/overrides và process exit code. Không phát lệnh/thu micro.
Qt Windows chụp hai tab mới; chữ đen trên nền tối đã tái hiện và có regression trước
sửa. Bằng chứng sau sửa: `build/restore-native-8817bc03308d406cadd06d3e7b7e77bd`.

Khởi động lại EXE dùng `PYINSTALLER_RESET_ENVIRONMENT=1` theo
[hướng dẫn PyInstaller](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart),
để child là instance độc lập. Fixture kiểm riêng source/frozen argv và giữ --debug.
Native factory đã kiểm bản nguồn; selftest runtime của EXE là kiểm bundle/ASR/TTS/VAD,
không thay thế kiểm thao tác từ micro hay benchmark giọng Nam.

## Tạo tài liệu trắng và cửa sổ (2026-10-12)

`python tools/verify_new_documents.py`: tạo trước code (50 fail, 1 pass), sau code 77 pass.
Kiểm parser, ngữ cảnh Word/Excel/PowerPoint, custom/override, chính tả, focus/cancel/timeout,
MSAA QueryInterface và giải phóng VARIANT. 20 lượt qua recorder/Controller/hybrid/parser/
executor với Google/offline/hybrid/fallback dùng transcript và Windows fakes.

`python tools/verify_new_documents_native.py`: ba Office ở editor/Home tạo đúng một tài
liệu trắng, chưa lưu; fixture cũ giữ nội dung. Trình duyệt ở profile riêng tạo tab, window
và private window đúng một; accessibility xác nhận InPrivate/Incognito, không chỉ đếm HWND.
15 checks đạt (6 quan sát Office, 3 tổng kết Office, 6 browser); evidence:
`build/new-documents-native-60e3e8e85b0046fbaf4ab37ad463919f/report.json`.
Office chỉ đóng tài liệu do harness tạo, chỉ Quit instance vốn không có tài liệu;
browser chỉ kết thúc PID sở hữu profile thử. Không thu micro hay đọc debug log.

Home dùng [Microsoft Active Accessibility](https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-accessibleobjectfromwindow)
và [AccessibleChildren](https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-accessiblechildren).
Child dispatch phải được QueryInterface thành IAccessible trước khi đọc children;
probe ban đầu sai interface gây access violation, đã có regression bằng fakes.
Office tại máy dùng nhãn template tiếng Anh; bộ nhãn tiếng Việt chưa có native đối chứng.
Phiên bản Office/ngôn ngữ khác và private mode bị chính sách doanh nghiệp chặn chưa được kiểm.
Native có STT fixture; không thay thế kiểm độ chính xác nhận giọng Nam.

## Đóng cửa sổ (2026-10-12)

`python tools/verify_close_window.py`: trước code 36 fail, sau code 37 pass;
kiểm parser/custom/prompt, chính tả, Desktop/taskbar/ancestor, lỗi probe,
focus/cancel trước và sau probe, bốn đường STT và một tác động mỗi lượt.
`python tools/verify_close_window_native.py` đạt ba checks: đóng Notepad đã lưu,
Notepad chưa lưu còn mở với hộp hỏi lưu, năm HWND Desktop/taskbar thật đều bị chặn.
Nhánh Desktop dùng key sender giả để tuyệt đối không phát Alt+F4 lên Desktop;
hai lượt Notepad đi qua Controller/parser/executor với SendInput thật.
Evidence: `build/close-window-native-f4e67ff991ee4248889b34b67dc1fb7e/report.json`.
Nhận diện dùng [GetAncestor](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getancestor),
[GetShellWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getshellwindow)
và [GetDesktopWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getdesktopwindow),
không dựa vào tiêu đề hay chặn toàn bộ Explorer.
