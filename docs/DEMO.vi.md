# Kịch bản demo VoicePilot

Tài liệu này dùng để demo trực tiếp và để quay video. Mỗi bước ghi rõ cửa sổ đang mở, câu nói và kết quả cần thấy. Kết quả được ghi vào bảng kiểm tra ở mục 3.

Ngày 2026-10-11, harness đã chạy đủ 15 chức năng với tác động Windows thật và ba vòng 10
chức năng bằng transcript fixture. Báo cáo `verification\20261011-010358-974613c8\report.md`
(LocalAppData) giữ bằng chứng. Đây chưa phải demo nói qua micro hoặc số đo p95 từ ngừng nói;
các ô vòng nói thật bên dưới được giữ trống. Native hotkey/micro và chính tả Unicode đã đạt.

## 1. Chuẩn bị

- Laptop Windows 10 với micro hoạt động. Thử `Ctrl + Alt + V` một lần trước khi quay.
- PowerPoint desktop, mở tệp `Demo-VoicePilot.pptx` có ít nhất 5 slide. Tệp chưa lưu lần nào để thử "lưu thành".
- Edge hoặc Chrome, một tab trống và hai tab khác để thử chuyển tab.
- Thư mục `Pictures\VoicePilot` có thể truy cập.
- Tắt thông báo Windows. Đặt DPI 100% hoặc 125%.
- Không có thông tin cá nhân trên màn hình (email, tên tài khoản, tab đang đăng nhập).

## 2. Kịch bản 15 chức năng

Thứ tự này giữ cho cửa sổ luôn đúng ngữ cảnh. Đưa PowerPoint lên trước cho các bước 1–6, chuyển sang Edge cho các bước 7–12, và dùng cửa sổ Windows cho các bước 13–15.

### 2.1 PowerPoint (bước 1–6)

| Bước | Cửa sổ đang mở | Nói | Kết quả mong đợi |
|---|---|---|---|
| 1 | PowerPoint, slide 1 | "bắt đầu trình chiếu" | Trình chiếu bắt đầu từ slide 1. |
| 2 | Trình chiếu, slide 1 | "slide tiếp theo" | Chuyển sang slide 2. |
| 3 | Trình chiếu, slide 2 | "slide trước" | Quay lại slide 1. |
| 4 | Trình chiếu | "dừng trình chiếu" | Về chế độ chỉnh sửa. |
| 5 | Chế độ chỉnh sửa | "lưu thành demo báo cáo" | Lưu thành `demo báo cáo.pptx` cạnh tệp hiện tại. |
| 6 | Chế độ chỉnh sửa | "lưu lại" | Lưu tệp hiện tại; thanh nổi báo đã lưu. |

### 2.2 Edge (bước 7–12)

| Bước | Cửa sổ đang mở | Nói | Kết quả mong đợi |
|---|---|---|---|
| 7 | Edge, tab trống | "mở youtube" | Mở YouTube trong tab mới. |
| 8 | Edge, tab YouTube | "tìm thời tiết hôm nay" | Tìm trên Google trong tab mới. |
| 9 | Edge, tab kết quả tìm kiếm | "tab trước" | Chuyển về tab trước. |
| 10 | Edge | "tab tiếp theo" | Chuyển sang tab tiếp theo. |
| 11 | Edge | "tab mới" | Mở tab trống mới. |
| 12 | Edge, tab mới | "đóng tab" | Đóng tab đang hoạt động. |

### 2.3 Windows (bước 13–15)

| Bước | Cửa sổ đang mở | Nói | Kết quả mong đợi |
|---|---|---|---|
| 13 | Desktop | "mở máy tính" | Mở Calculator. |
| 14 | Desktop | "chụp màn hình" | Lưu PNG vào `Pictures\VoicePilot`; thanh nổi không xuất hiện trong ảnh. |
| 15 | Notepad mở sẵn | Nhấn `Ctrl + Alt + M`, rồi nói "Xin chào, đây là bài kiểm thử chính tả." | Câu nói được chèn vào Notepad tại con trỏ. Nhấn `Ctrl + Alt + M` để quay lại chế độ lệnh. |

Bước 13 và 14 có thể chạy trước bước 1 nếu cần đổi cửa sổ. Ghi lại thứ tự thực tế nếu khác kịch bản.

## 3. Kiểm tra độ ổn định

Chọn **10 chức năng ổn định nhất** sau vòng thử đầu tiên và đánh dấu cột "Top 10". Chạy 10 chức năng đó **ba vòng liên tiếp**. Mỗi vòng bắt đầu từ trạng thái ban đầu của kịch bản (PowerPoint ở slide 1, Edge một tab trống).

**Tiêu chí đạt cho một lần thử:**

- Đúng chức năng và đúng kết quả ở lần nói đầu tiên, hoặc sau tối đa một lần nói lại do lỗi nhận dạng.
- Phản hồi trong 4 giây sau khi ngừng nói (đo bằng đồng hồ).
- Không thao tác nhầm cửa sổ khác và không hiện lỗi.

| # | Chức năng | Top 10 | Vòng 1 | Vòng 2 | Vòng 3 | Ghi chú |
|---|---|---|---|---|---|---|
| 1 | Bắt đầu trình chiếu | ☐ | ☐ | ☐ | ☐ | |
| 2 | Slide tiếp | ☐ | ☐ | ☐ | ☐ | |
| 3 | Slide trước | ☐ | ☐ | ☐ | ☐ | |
| 4 | Dừng trình chiếu | ☐ | ☐ | ☐ | ☐ | |
| 5 | Lưu tên khác | ☐ | ☐ | ☐ | ☐ | |
| 6 | Lưu | ☐ | ☐ | ☐ | ☐ | |
| 7 | Mở website | ☐ | ☐ | ☐ | ☐ | |
| 8 | Tìm Google | ☐ | ☐ | ☐ | ☐ | |
| 9 | Tab tiếp | ☐ | ☐ | ☐ | ☐ | |
| 10 | Tab trước | ☐ | ☐ | ☐ | ☐ | |
| 11 | Tab mới | ☐ | ☐ | ☐ | ☐ | |
| 12 | Đóng tab | ☐ | ☐ | ☐ | ☐ | |
| 13 | Mở ứng dụng | ☐ | ☐ | ☐ | ☐ | |
| 14 | Chụp màn hình | ☐ | ☐ | ☐ | ☐ | |
| 15 | Đọc chính tả | ☐ | ☐ | ☐ | ☐ | |

Ghi lại tổng số lần đạt trong ba vòng cho mỗi chức năng trong mục "Ghi chú". Một chức năng có lỗi trong bất kỳ vòng nào phải được ghi kèm nguyên nhân và thời điểm.

## 4. Kịch bản video (khoảng 4 phút)

| Thời gian | Nội dung | Ghi chú quay |
|---|---|---|
| 0:00–0:20 | Giới thiệu: VoicePilot là gì, chạy offline được không, mục tiêu. | Quay màn hình có thanh nổi ở đáy màn hình. |
| 0:20–0:40 | Sơ đồ luồng: phím tắt → micro → nhận dạng hybrid → phân tích lệnh → thực thi → phản hồi. | Chèn sơ đồ từ README (Mermaid) hoặc slide riêng. |
| 0:40–1:40 | PowerPoint: bước 1–6. | Quay cận cảnh thanh nổi hiện chế độ, cửa sổ đích và câu nhận dạng. |
| 1:40–2:40 | Edge và Windows: bước 7–14. | Cho thấy tab mới mở đúng cửa sổ đích. |
| 2:40–3:10 | Đọc chính tả: bước 15 và chuyển chế độ. | Quay Notepad. |
| 3:10–3:40 | Quyền riêng tư và ngoại tuyến: tắt Wi-Fi, chạy lệnh PowerPoint và chụp màn hình. Giải thích rằng chế độ Google gửi âm thanh đi, còn Gipformer thì không. | Ghi rõ trạng thái Wi-Fi trong video. |
| 3:40–4:00 | Kết quả đo: bảng độ chính xác và độ trễ từ `docs/BENCHMARKS.md`, nguồn repo, giấy phép GPL-3.0. | Hiển thị bảng kết quả thật, không dùng số ước tính. |

**Lưu ý khi quay:**

- Quay ở 1920×1080, DPI 100% hoặc 125%. Kiểm tra thanh nổi không che nội dung quan trọng.
- Mỗi lệnh quay một lần để giữ nhịp; nếu phải làm lại thì cắt bỏ đoạn lỗi và ghi chú trong video description.
- Không để lộ thông báo, email, tên tài khoản hoặc đường dẫn cá nhân trong khung hình.
