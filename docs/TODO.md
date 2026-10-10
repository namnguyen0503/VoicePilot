# VoicePilot — việc còn lại trước khi phát hành

Cập nhật 2026-10-10. Nam chốt phạm vi 15 chức năng v1; để kiểm tra desktop sau.
Các phép thử giả lập/âm thanh tổng hợp không thay cho demo giọng nói thật.

## 1. Còn cần xác minh trên Windows

- [ ] Desktop mở: overlay không lấy focus, gõ tiếng Việt/emoji trong Notepad/Word/Edge/Chrome,
  đổi foreground trong lúc xử lý, cửa sổ admin/UIPI. Harness đã có probe Notepad/focus.
- [ ] PowerPoint: GetActiveObject, Save/SaveAs tên tiếng Việt, chuyển slide ở editor/slideshow,
  bắt đầu/dừng chiếu, timeout COM. Harness dùng presentation mới; chưa có native pass.
- [ ] Tab Edge/Chrome trên profile thử, mở web/tìm kiếm qua ShellExecute; screenshot giấu overlay
  và DPI 125–150%. Không chạy khi desktop khóa; `blocked` không tính là đạt.
- [ ] Đối chứng app/cửa sổ sau lệnh mở ứng dụng, gồm AppsFolder/Store. Lượt debug mới nhất đã có
  parser khớp nhiều app; chưa có chứng cứ đối chiếu cửa sổ cho toàn bộ luồng giọng nói.
- [ ] Đo 30 câu nói thật theo `docs/BENCHMARKS.md`, dùng `docs/benchmark-labels.csv`.
  Chạy riêng Google/offline/hybrid, điền WER, intent/params accuracy, p50/p95, RAM.
  Tên tiếng Anh Hugging Face/Claude cần clip có câu tham chiếu đúng; không suy đoán alias.
- [ ] Đo độ trễ từ ngừng nói đến phản hồi và demo đủ 15 chức năng; ba vòng 10 chức năng ổn định
  nhất theo PLAN. Chưa kết luận đạt p95 4 giây từ smoke synthetic.
- [ ] Nghe TTS câu dài, số, tên riêng, dấu câu; chất lượng đọc chính tả trên giọng Nam.
- [ ] ZIP trên máy sạch không Python, có/không mạng; tải endpoint model qua proxy/CA tổ chức
  thực tế nếu có. Harness loopback TLS/proxy có auth đã đạt sáu tình huống trên Windows.
- [ ] CI trên GitHub sau khi repo có remote. Workflow Windows đã được thêm và kiểm tra nội dung;
  checkout hiện không có remote nên chưa có GitHub run.

## 2. Còn cần chốt trước phát hành công khai

- [ ] Quyền phân phối voice `vi_VN-vais1000-medium`: model card nói fine-tune từ Lessac;
  model card Lessac dẫn tới giấy phép dataset chỉ dùng nghiên cứu/hạn chế phân phối.
  Điều khoản dataset không đủ để kết luận quyền phân phối model weights. Cần làm rõ với
  publisher/quyền sở hữu; chưa coi ZIP kiểm chứng tại máy là bản sẵn sàng phát hành công khai.
- [ ] Provenance chính xác của wheel espeak-ng trong sherpa-onnx-core và nguồn tương ứng.
  Recipe v1.13.8 pin commit `ed530aa113046142eb5115cf2fc9157854d0ffe1`, version 1.52.0.1,
  header GPL-3.0-or-later đã xác minh. Recipe không chứng minh wheel có đúng build đó.
- [ ] Giữ nguồn tương ứng và các nghĩa vụ phân phối GPL/FLAC khi phát hành. FLAC đi riêng,
  `--version` là 1.3.2; GPL-2.0 được giữ trong wheel metadata. Chưa kết luận chỉ gói chung ZIP
  là hoàn thành mọi nghĩa vụ phân phối.
- [ ] Điền `-RepoUrl` thật vào build, xác nhận commit/source tương ứng và chạy CI.
  Repo hiện không có remote; `SOURCE.txt` của ZIP kiểm chứng giữ placeholder có chủ đích.

Chi tiết nguồn và giấy phép: `THIRD_PARTY_NOTICES.md`.

## 3. Đã xác minh ngày 2026-10-10

- Harness được tạo/chạy trước khi sửa app. Đủ 15 intent có kiểm tra tác động parser → executor
  với fakes; test chứng minh harness phát hiện executor lỗi và giữ trạng thái fail/blocked.
- Pytest 1172 pass, 2 skip; ruff sạch. Pytest dùng basetemp mới và tắt cache để tránh ACL/cache
  cũ ở máy Nam. Không xóa thư mục test cũ bị lỗi quyền.
  Bản copy có đường dẫn dài: 1145 pass, 3 skip; thêm một fixture thư mục 150 ký tự tự bỏ qua
  vì tmp_path đã quá dài. Hai skip mặc định Windows kiểm đường dẫn cấu hình của OS khác.
- Python 3.12.5, PySide6-Essentials 6.12.0; cài venv mới trong đường dẫn tiếng Việt. 14 runtime
  distribution khớp `requirements.lock`; không có package onnxruntime thứ hai.
- Gipformer và Piper TTS cùng tiến trình nhận dạng được câu tổng hợp thành intent screenshot;
  decoder chính tả không hotwords trả văn bản. `--selftest-runtime` đưa phép thử vào exe portable.
- Build PowerShell 5.1/PyInstaller: webrtcvad-wheels hook, tránh ICU từ PATH Poppler, Python UTF-8,
  version metadata và wheel license texts. Các lỗi Windows đều có regression với fakes.
- Đã tạo ZIP ~178 MB ở đường dẫn thường và có dấu/khoảng trắng bằng PowerShell 5.1.
  Exe `--selftest-runtime` đạt ASR/TTS cùng tiến trình, intent và chính tả, cả config_dir có dấu.
  Phép thử sâu tìm lỗi espeak/BPE Unicode; đã sửa short path/cache ASCII, kiểm cả fallback native.
- Bundle có sherpa/onnxruntime, sáu DLL PortAudio, FLAC, pywin32, plugin qsvg, 15 metadata folders
  và 19 mục license. PocketSphinx đã loại bằng hook riêng; FLAC thực chuyển PCM thành bitstream hợp lệ.
- Token Gipformer ở revision `54fa79b1fb790422fa27b2f1a771161f9521ca48`: SHA-256
  `f536d03c2e95ebd2930cf0abec88e823bd17d3c1933da7ae6a82db3b80605e15`, khớp bản tải upstream
  và local; đã pin trong models.toml. Ba weight hash không thay đổi.
- Archive TTS SHA-256 `fa1367710767d36ed5cf13b4a449e20c35ffd12791c2e47c2e64142bfa55551a`
  đã tải/kiểm trước đó; espeak-ng-data nằm trong models/tts cạnh exe, ngoài _internal.
- LICENSE khớp byte với GNU Coreutils COPYING: 35 149 byte, SHA-256
  `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`.
- cffi 2.1.1 là MIT-0 (MIT No Attribution); pycparser 3.1 là BSD-3-Clause, xác minh wheel LICENSE.
- Các câu chuyển slide/tab, tên Photoshop bỏ tiền tố Adobe và chính tả không hotwords đã sửa.
  Website ưu tiên cho “mở Zalo”; dùng “mở ứng dụng Zalo”/“mở trang Zalo” để chọn đích.
- Thêm 60 lượt qua recorder/Controller/hybrid thật với PCM và tác động Windows giả lập:
  đủ 15 chức năng ở Google/offline/hybrid/fallback đạt. Hai lỗi Google nhầm slide/tab ở app khác
  đã tái hiện trước sửa rồi đạt sau sửa. Validator chung với benchmark; đổi đích không gây tác động.
- TTS native kiểm callback qua player im lặng: đã sửa đảo quy ước 1=tiếp tục, 0=dừng.
  Fake được sửa theo API thật và bắt lỗi trước khi sửa speaker; đủ câu và hủy callback đều đạt.
- Worker PowerPoint: 33 tình huống COM giả lập đạt; harness tái hiện lỗi trước sửa.
  Timeout không để job đang chờ chạy muộn; đổi đích trong lúc lấy COM/đọc thuộc tính chặn tác động.
  `Run()` chậm không gọi tiếp `GotoSlide`; lời gọi COM đã bắt đầu không thể thu hồi.
- `models/` được ignore toàn bộ, gồm dữ liệu phoneme đã giải nén; không commit model/bundle/venv.
- Downloader CLI thật qua proxy CONNECT có Basic auth và CA riêng: redirect, archive, từ chối
  CA không tin cậy, sai hash giữ file cũ, retry và cache đều đạt; không đổi mạng/trust store Windows.
- ZIP giải nén vào đường dẫn có dấu chạy ASR/TTS/chính tả với PATH chỉ Windows, không dùng
  biến Python/model ngoài; 125 module quan sát thuộc bundle hoặc Windows. Python/ONNX Runtime
  đến từ bundle. Thư mục model không tồn tại cho lỗi đúng; máy sạch vẫn chờ kiểm riêng.

## 4. Cách tiếp tục

Chạy `python tools/verify_features.py --checks --runtime` để lấy báo cáo mới trong
`%LOCALAPPDATA%\VoicePilot\verification\`. Chỉ thêm `--windows` khi Nam cho phép kiểm desktop,
máy mở khóa và không thao tác. Hướng dẫn và giới hạn: `docs/VERIFICATION.vi.md`.

Nam chạy app với `--debug` rồi nói “xem log đi”: chỉ đọc session/lượt mới nhất trong
`%LOCALAPPDATA%\VoicePilot\logs\debug.log` và clip tương ứng. Mọi lỗi Windows mới phải có regression;
đọc diff, chạy pytest và ruff trước mỗi commit.

Benchmark đo STT/parser, chưa gồm vẽ UI/thu âm; RAM đỉnh chỉ so sánh đúng khi chạy riêng từng backend.
Lần chính tả offline đầu nạp decoder riêng, tăng độ trễ và RAM; báo cáo phải ghi điều kiện warm/cold.
