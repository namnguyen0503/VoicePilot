# Harness kiểm chứng VoicePilot

Chạy harness trước khi sửa app để giữ kết quả nền, rồi chạy lại sau sửa:

```powershell
.\.venv\Scripts\python.exe tools\verify_features.py --checks --windows
```

Không có `--windows`: chỉ kiểm tra parser → executor với backend giả lập cho đủ
15 chức năng. `--checks` chạy thêm pytest, ruff và selftest. Pytest dùng thư mục
tạm mới cho mỗi lần chạy, tránh quyền truy cập/cache cũ của lần chạy trước.

Với `--windows`, harness mở Notepad, PowerPoint và profile thử riêng cho Edge/Chrome.
Nó tạo tài liệu mới để thử, đóng những cửa sổ nó đã tạo sau khi xong. Không dùng
tài liệu đang làm hoặc profile trình duyệt cá nhân. Trong lúc chạy không thao tác
bàn phím/chuột để kết quả focus có thể lặp lại.

Báo cáo JSON/Markdown và đầu ra công cụ nằm dưới
`%LOCALAPPDATA%\VoicePilot\verification\<thời điểm>-<id>\`.
Dùng `--output <thư mục mới>` để chọn nơi lưu. Kết quả `blocked` nghĩa là thiếu
điều kiện kiểm thử, không được tính là đạt. Exit code khác 0 nếu có fail hoặc blocked.

Harness quan sát chữ tiếng Việt/emoji trong Notepad; focus trước/sau khi overlay
hiện; chặn đích đã đổi; PNG và kích thước màn hình; cửa sổ app sau khi mở;
tệp PowerPoint, trạng thái đã lưu, slide và trình chiếu; số tab/URL và tiêu đề
tab trên profile riêng của từng trình duyệt. Mở web/tìm kiếm dùng ShellExecute thật
với tham số profile thử riêng. Các kiểm tra backend giả lập được ghi nhãn `fake`.

Harness chưa thay cho thử micro thật, chất lượng giọng đọc, Word, cửa sổ admin/UIPI,
DPI 125–150% hoặc máy sạch không Python. Những điều kiện đó cần chạy riêng và ghi
bằng chứng cụ thể. Dữ liệu tổng hợp không được tính là benchmark giọng nói của Nam.
