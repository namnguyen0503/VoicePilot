# Harness kiểm chứng VoicePilot

Chạy harness trước khi sửa app để giữ kết quả nền, rồi chạy lại sau sửa:

```powershell
.\.venv\Scripts\python.exe tools\verify_features.py --checks --windows
```

Không có `--windows`: chỉ kiểm tra parser → executor với backend giả lập cho đủ
15 chức năng. `--checks` chạy thêm pytest, ruff và selftest. Pytest dùng thư mục
tạm mới cho mỗi lần chạy, tránh quyền truy cập/cache cũ của lần chạy trước.

Thêm `--runtime` để kiểm tra nạp ASR/TTS trong cùng tiến trình và nhận dạng
âm thanh tổng hợp qua decoder lệnh và decoder chính tả không có hotwords.
Chế độ này chạy được khi desktop khóa; kết quả là smoke test, không phải benchmark
giọng thật. `--windows` ghi `blocked` và không mở cửa sổ khi máy đang khóa.

Với `--windows`, harness mở Notepad, PowerPoint và profile thử riêng cho Edge/Chrome.
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

Harness chưa thay cho thử micro thật, chất lượng giọng đọc, Word, cửa sổ admin/UIPI,
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
