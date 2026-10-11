# Danh sách lệnh VoicePilot

VoicePilot nghe lệnh bằng tiếng Việt và thực hiện trên PowerPoint, Edge/Chrome và Windows.

## Cách dùng nhanh

- Nhấn **Ctrl + Alt + Space** để bắt đầu nghe, nói lệnh, rồi chờ phản hồi. Nhấn lại **Ctrl + Alt + Space** để kết thúc sớm. Nếu bạn im lặng khoảng **0,8 giây** sau khi nói, VoicePilot tự kết thúc lượt.
- Trong **Cài đặt**, bật **Tự dừng khi ngừng nói**. Mặc định dừng sau 800 ms im lặng;
  có thể tăng khoảng này khi đọc chính tả và hay nghỉ giữa câu. Tắt tùy chọn để kết thúc
  bằng phím nghe lần nữa hoặc hết thời lượng. Không nói trong 5 giây thì tự hủy lượt nghe.
- Nhấn **Ctrl + Alt + D** để chuyển giữa **Ra lệnh** và **Đọc chính tả**.
- Mỗi lần chỉ nói **một câu, một lệnh**. Các từ lịch sự ở đầu hoặc cuối câu sẽ bị bỏ qua:
  - Ở đầu câu: "làm ơn", "giúp tôi", "giúp mình", "giùm", "giùm tôi", "giùm mình", "cho tôi", "cho mình", "hãy", "ơi".
  - Ở cuối câu: "làm ơn", "giúp tôi", "giúp mình", "giùm", "giùm tôi", "giùm mình", "nhé", "ạ", "ơi".
  - Với lệnh ngắn hoặc tên website, tên ứng dụng: "đi" và "với" ở cuối câu cũng bị bỏ qua, ví dụ "mở youtube đi" hay "slide tiếp với".
  - Với nội dung tìm kiếm, tên tệp: chỉ bỏ các từ lịch sự, còn "đi" và "với" được giữ lại. Ví dụ "tìm đường đi" tìm đúng "đường đi".
- Muốn đổi phím tắt, vào mục **Cài đặt** của VoicePilot.

## Khi nào lệnh chạy

| Nhóm lệnh | Điều kiện |
|---|---|
| PowerPoint | Chỉ chạy khi cửa sổ PowerPoint đang ở phía trước. |
| Tab (tab tiếp, tab mới, đóng tab…) | Cần cửa sổ Edge hoặc Chrome đang ở phía trước. |
| Mở website, tìm kiếm | Chạy từ mọi cửa sổ. Nếu không có Edge hoặc Chrome đang mở, dùng trình duyệt mặc định. |
| Mở ứng dụng, chụp màn hình | Chạy từ mọi cửa sổ. |

## 1. PowerPoint

Chỉ chạy khi cửa sổ PowerPoint đang ở phía trước.

| Chức năng | Cách nói | Ghi chú |
|---|---|---|
| Lưu | `lưu`, `lưu lại`, `lưu file`, `save` | Lưu bài trình chiếu đang mở. Với bài chưa có tên, nên dùng "lưu thành" để đặt tên. |
| Lưu thành tên khác | `lưu thành Báo cáo tháng 10`<br>`lưu tên khác là Hợp đồng 2026`<br>`lưu với tên Kế hoạch 10 năm`<br>`lưu thành Bài thuyết trình, chấm pptx` | Tên tệp giữ nguyên như bạn nói, VoicePilot tự thêm `.pptx`. Ngoặc đi kèm được giữ, ví dụ "Báo cáo Q3 (bản cuối)". Nếu nói thêm "chấm pptx" hoặc ".pptx" ở cuối, phần đó bị bỏ đi. Trùng tên sẽ tự thêm `(2)`, `(3)`… Nói "lưu thành" mà không kèm tên thì VoicePilot sẽ nhắc nói thêm tên. |
| Slide tiếp | `slide tiếp theo`, `trang tiếp`, `chuyển slide`, `chuyển slide tiếp theo`, `chuyển sang slide tiếp theo`, `qua slide`, `next slide` | Không nên chỉ nói "tiếp theo"; xem mục "Từ mơ hồ" bên dưới. |
| Slide trước | `slide trước`, `trang trước`, `quay lại slide`, `lùi slide`, `previous slide` | Không nên chỉ nói "quay lại"; xem mục "Từ mơ hồ" bên dưới. |
| Bắt đầu trình chiếu | `bắt đầu trình chiếu`, `trình chiếu`, `chiếu slide`, `start show` | Bắt đầu từ slide đầu tiên. |
| Dừng trình chiếu | `dừng trình chiếu`, `kết thúc trình chiếu`, `thoát trình chiếu`, `đóng trình chiếu`, `end show` | Trở về chế độ chỉnh sửa. |

## 2. Trình duyệt (Edge và Chrome)

| Chức năng | Cách nói | Ghi chú |
|---|---|---|
| Mở website | `mở youtube`, `vào facebook`, `mở trang vnexpress`, `mở vnexpress.net`, `mở https://example.com` | Chạy từ mọi cửa sổ. Tên phải có trong danh sách website bên dưới, hoặc là tên miền như `mở vnexpress.net` hoặc `mở github.com/abc`. Chỉ `http://` và `https://` được mở trực tiếp; các giao thức khác như `ftp://` không được mở. Tên tệp như `notepad.exe` và đường dẫn ổ đĩa như `C:\Windows\...` không được mở như website. Tên lạ sẽ không mở được. Chỉ nói "mở trang" sẽ được hỏi lại. |
| Tìm kiếm | `tìm thời tiết hôm nay`, `tìm kiếm giá vàng`, `google giá vàng`, `tra cứu lịch thi học kỳ` | Chạy từ mọi cửa sổ. Câu tìm kiếm giữ nguyên như bạn nói. Chỉ nói "tìm" sẽ được hỏi lại nội dung. |
| Tab tiếp | `tab tiếp theo`, `tab sau`, `chuyển tab`, `chuyển tab tiếp theo`, `chuyển sang tab tiếp theo`, `next tab` | Cần Edge hoặc Chrome ở phía trước. |
| Tab trước | `tab trước`, `quay lại tab`, `chuyển về tab trước`, `lùi tab`, `previous tab` | Cần Edge hoặc Chrome ở phía trước. |
| Tab mới | `tab mới`, `mở tab mới`, `thêm tab`, `tab trống` | Cần Edge hoặc Chrome ở phía trước. |
| Đóng tab | `đóng tab`, `đóng tab này`, `tắt tab`, `close tab` | Cần Edge hoặc Chrome ở phía trước. Đóng tab đang hoạt động. |

## 3. Windows

| Chức năng | Cách nói | Ghi chú |
|---|---|---|
| Mở ứng dụng | `mở word`, `mở excel`, `mở powerpoint`, `khởi động notepad`, `chạy máy tính`, `mở ứng dụng Word` | Chạy từ mọi cửa sổ. Tên ứng dụng phải có trong danh sách bên dưới. Chỉ nói "mở" sẽ được hỏi lại. |
| Chụp màn hình | `chụp màn hình`, `chụp ảnh màn hình`, `chụp màn` | Chạy từ mọi cửa sổ. Ảnh PNG được lưu vào `Pictures\VoicePilot`. |

## 4. Đọc chính tả

| Chức năng | Cách dùng | Ghi chú |
|---|---|---|
| Chèn văn bản | Nhấn **Ctrl + Alt + D** để vào chế độ Đọc chính tả, rồi nói nội dung cần chèn. | Văn bản được chèn tại vị trí con trỏ. Trong chế độ này mọi câu nói đều là văn bản, không chạy như lệnh: nói "mở Word" sẽ chèn đúng chữ "mở Word". Nhấn **Ctrl + Alt + D** lần nữa để quay lại Ra lệnh. |

## Từ mơ hồ: cửa sổ đang dùng quyết định

Một số cụm từ có thể hiểu theo nhiều lệnh. Với hai cụm "tiếp theo" và "quay lại", VoicePilot chọn lệnh theo cửa sổ đang ở phía trước.

| Bạn nói | PowerPoint ở phía trước | Edge hoặc Chrome ở phía trước | Cửa sổ khác |
|---|---|---|---|
| `tiếp theo` | Slide tiếp | Tab tiếp | Không hiểu, gợi ý một câu mẫu |
| `quay lại` | Slide trước | Tab trước | Không hiểu, gợi ý một câu mẫu |
| `đóng` | Không chạy (đây là lệnh đóng tab) | Đóng tab | Không chạy (đây là lệnh đóng tab) |
| `mở mới` | Không chạy (đây là lệnh tab mới) | Tab mới | Không chạy (đây là lệnh tab mới) |

Hai cụm "đóng" và "mở mới" luôn là lệnh tab, nên chỉ chạy khi Edge hoặc Chrome đang ở phía trước. Để tránh nhầm, hãy nói rõ: "slide tiếp theo" hoặc "tab tiếp theo", "đóng trình chiếu" hoặc "đóng tab".

## Tên website

Danh sách dưới đây là tên có sẵn. Bạn có thể thêm tên khác trong mục `[websites]` của `%LOCALAPPDATA%\VoicePilot\config.toml`.

| Tên nói | Địa chỉ |
|---|---|
| youtube | https://www.youtube.com |
| facebook | https://www.facebook.com |
| google | https://www.google.com |
| gmail | https://mail.google.com |
| github | https://github.com |
| chatgpt | https://chatgpt.com |
| claude (cloud, cờ lốt, claude chấm ai) | https://claude.ai |
| youtube music | https://music.youtube.com |
| hugging face (hắc ging phây) | https://huggingface.co |
| vnexpress | https://vnexpress.net |
| zalo | https://chat.zalo.me |
| shopee | https://shopee.vn |
| wikipedia | https://vi.wikipedia.org |

Để mở trang khác, nói địa chỉ có dấu chấm, ví dụ "mở vnexpress.net" hoặc "mở github.com".

## Tên ứng dụng

Danh sách dưới đây là tên có sẵn. Bạn có thể thêm ứng dụng trong mục `[apps]` của `config.toml`.

Ứng dụng đã cài trên máy mà không có trong danh sách cũng mở được bằng "mở <tên ứng dụng>", ví dụ "mở spotify", nếu tên đó có trong menu Start. Nếu không tìm thấy, VoicePilot sẽ báo chưa hiểu.

Khi một tên có cả website và ứng dụng, hãy nói rõ **"mở ứng dụng Zalo"** để mở
Zalo desktop, hoặc **"mở trang Zalo"** để mở web. "Mở Zalo" giữ ưu tiên website.
Với ứng dụng như "Adobe Photoshop 2026", có thể nói "mở Photoshop".
Trong chính tả, Gipformer dùng decoder không có hotwords của lệnh/website/app;
decoder riêng chỉ tải khi cần nhận dạng chính tả offline.

| Tên nói | Ứng dụng |
|---|---|
| powerpoint (microsoft powerpoint) | Microsoft PowerPoint |
| word (microsoft word) | Microsoft Word |
| excel (microsoft excel) | Microsoft Excel |
| edge | Microsoft Edge |
| chrome | Google Chrome |
| notepad | Notepad |
| máy tính | Máy tính |

## Khi VoicePilot chưa hiểu

Nếu VoicePilot không hiểu, thanh nổi sẽ hiện **"Thử nói: …"** kèm một câu mẫu. Hãy nói lại theo câu mẫu đó.

- Nói ngắn gọn, mỗi lần một lệnh.
- Kiểm tra cửa sổ đang ở phía trước: lệnh PowerPoint cần PowerPoint, lệnh tab cần Edge hoặc Chrome.
- Với lệnh cần nội dung (tìm kiếm, lưu thành, mở ứng dụng), nói kèm nội dung, ví dụ "tìm giá vàng" thay vì chỉ "tìm".
- Nếu tên website hoặc ứng dụng không được nhận, thêm tên đó vào `config.toml` như mô tả ở trên.
