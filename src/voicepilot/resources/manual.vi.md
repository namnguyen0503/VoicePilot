# Hướng dẫn sử dụng VoicePilot

VoicePilot nghe lệnh bằng tiếng Việt và thực hiện trên PowerPoint, Edge/Chrome và Windows.

## Cách dùng nhanh

- Nhấn **Ctrl + Alt + Space** để bắt đầu nghe, nói lệnh, rồi chờ phản hồi. Nhấn lại **Ctrl + Alt + Space** để kết thúc sớm. Nếu bạn im lặng khoảng **0,8 giây** sau khi nói, VoicePilot tự kết thúc lượt.
- Trong **Cài đặt**, bật **Tự dừng khi ngừng nói**. Mặc định dừng sau 800 ms im lặng;
  có thể tăng khoảng này khi đọc chính tả và hay nghỉ giữa câu. Tắt tùy chọn để kết thúc
  bằng phím nghe lần nữa hoặc hết thời lượng. Không nói trong 5 giây thì tự hủy lượt nghe.
- Bộ phát hiện tiếng nói Silero chạy offline giúp tự dừng trong tiếng quạt, nhiễu nền
  và tiếng gõ phím. Khoảng chờ tính từ lúc bộ phát hiện xác nhận hết tiếng nói nên thực tế
  có thể dài hơn 800 ms. Tiếng TV/người khác nói vẫn có thể được nhận là tiếng nói;
  khi tiếng ồn lấn át giọng, hãy đưa micro gần miệng hoặc nhấn phím nghe để kết thúc sớm.
- Nhấn **Ctrl + Alt + D** để chuyển giữa **Ra lệnh** và **Đọc chính tả**.
- Mỗi lần chỉ nói **một câu, một lệnh**. Các từ lịch sự ở đầu hoặc cuối câu sẽ bị bỏ qua:
  - Ở đầu câu: "làm ơn", "giúp tôi", "giúp mình", "giùm", "giùm tôi", "giùm mình", "cho tôi", "cho mình", "hãy", "ơi".
  - Ở cuối câu: "làm ơn", "giúp tôi", "giúp mình", "giùm", "giùm tôi", "giùm mình", "nhé", "ạ", "ơi".
  - Với lệnh ngắn hoặc tên website, tên ứng dụng: "đi" và "với" ở cuối câu cũng bị bỏ qua, ví dụ "mở youtube đi" hay "slide tiếp với".
  - Với nội dung tìm kiếm, tên tệp: chỉ bỏ các từ lịch sự, còn "đi" và "với" được giữ lại. Ví dụ "tìm đường đi" tìm đúng "đường đi".
- Muốn đổi phím tắt, vào mục **Cài đặt** của VoicePilot.

## Voice và Text

Thanh điều khiển có hai tab **Voice** và **Text**. Voice dùng micro; Text có ô nhập dài,
nhấn **Enter** hoặc nút **➤** để gửi. Trong Text, VoicePilot tắt thu âm; phím nghe chỉ
mở lại ô nhập. Cả hai tab dùng chung lệnh có sẵn, lệnh tùy chỉnh và chế độ chính tả.
Khi đang xử lý, ô nhập/nút gửi bị khóa để một lượt chỉ thực hiện một lần.

Chọn ứng dụng đích trước khi mở Text. Khi gửi, VoicePilot trả focus về cửa sổ đã chọn.
Nếu bạn chuyển sang cửa sổ khác trong lúc nhập hoặc đóng cửa sổ đích, lượt bị chặn;
hãy chọn lại ứng dụng và mở Text bằng phím nghe. Với chính tả, text được chèn vào
con trỏ của ứng dụng đích, giữ dấu tiếng Việt và emoji.

Bấm nút **Ra lệnh / Chính tả**, hoặc phím đổi chế độ, ngay khi đang nói. Âm thanh đã
thu được giữ lại và cả lượt dùng chế độ mới. Nếu nhận dạng/thực thi đã bắt đầu,
lượt cũ bị hủy; hãy bắt đầu lượt mới theo chế độ đã chọn.

Trong **Cài đặt → Tab khi mở lại**, chọn:

- **Mặc định về Voice**: mỗi lần mở lại bảng nhập dùng Voice (mặc định).
- **Nhớ tab đã chọn lần cuối**: mở lại Voice hoặc Text theo lựa chọn trước đó.
- **Luôn mở Text**: mỗi lần mở lại dùng Text; vẫn có thể bấm tab Voice cho lượt hiện tại.

Mở tray **Hướng dẫn sử dụng / Danh sách lệnh** để xem cửa sổ này trong app.
Có danh mục ở bên trái và ô tìm kiếm; phím tắt trong cửa sổ phản ánh cấu hình hiện tại.

## Khi nào lệnh chạy

| Nhóm lệnh | Điều kiện |
|---|---|
| PowerPoint | Chỉ chạy khi cửa sổ PowerPoint đang ở phía trước. |
| Tạo tài liệu Word/Excel | Cần đúng cửa sổ Word hoặc Excel ở phía trước, kể cả màn hình Home. |
| Tab (tab tiếp, tab mới, đóng tab…) | Cần cửa sổ Edge hoặc Chrome đang ở phía trước. |
| Cửa sổ mới / ẩn danh | Cần Edge hoặc Chrome ở phía trước; mở bằng chính trình duyệt đang dùng. |
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
| Tạo bài trình chiếu trắng | `tạo bài trình chiếu mới`, `tạo file PowerPoint mới`, `tạo bản trình bày mới`, `blank presentation` | Ở Home chọn Blank presentation; ở màn hình chỉnh sửa dùng Ctrl+N. Tài liệu mới chưa được lưu. |

### Tạo tài liệu Word và Excel

| Chức năng | Cách nói | Ghi chú |
|---|---|---|
| Word: tài liệu trắng | `tạo tài liệu Word mới`, `tạo file Word mới`, `tạo tài liệu mới`, `blank document` | Cần Word ở phía trước. Home chọn Blank document; màn hình chỉnh sửa dùng Ctrl+N. |
| Excel: bảng tính trắng | `tạo bảng tính mới`, `tạo file Excel mới`, `tạo bảng tính Excel mới`, `blank workbook` | Cần Excel ở phía trước. Home chọn Blank workbook; màn hình chỉnh sửa dùng Ctrl+N. |

Có thể nói chung **"tạo file mới"**, **"tạo tệp mới"**, **"file mới"** hoặc **"tạo file trắng"**;
VoicePilot chọn Word/Excel/PowerPoint theo cửa sổ đang dùng. Tài liệu mới chưa có tên và
chưa được ghi xuống đĩa; nội dung tài liệu đang mở được giữ nguyên. Lệnh cần ứng dụng Office
desktop đang mở; không tự chọn ứng dụng khi đang ở cửa sổ khác. Lệnh này không tạo slide mới
trong bài hiện tại. Có thể đổi cách gọi ở Prompt mặc định hoặc dùng hành động trong Lệnh custom.

## 2. Trình duyệt (Edge và Chrome)

| Chức năng | Cách nói | Ghi chú |
|---|---|---|
| Mở website | `mở youtube`, `vào facebook`, `mở trang vnexpress`, `mở vnexpress.net`, `mở https://example.com` | Chạy từ mọi cửa sổ. Tên phải có trong danh sách website bên dưới, hoặc là tên miền như `mở vnexpress.net` hoặc `mở github.com/abc`. Chỉ `http://` và `https://` được mở trực tiếp; các giao thức khác như `ftp://` không được mở. Tên tệp như `notepad.exe` và đường dẫn ổ đĩa như `C:\Windows\...` không được mở như website. Tên lạ sẽ không mở được. Chỉ nói "mở trang" sẽ được hỏi lại. |
| Tìm kiếm | `tìm thời tiết hôm nay`, `tìm kiếm giá vàng`, `google giá vàng`, `tra cứu lịch thi học kỳ`, `giá vàng hôm nay bao nhiêu`, `thời tiết Hà Nội hôm nay`, `Python là gì` | Chạy từ mọi cửa sổ. Câu tìm kiếm giữ nguyên như bạn nói. Chỉ nói "tìm" sẽ được hỏi lại nội dung. |
| Tab tiếp | `tab tiếp theo`, `tab sau`, `chuyển tab`, `chuyển tab tiếp theo`, `chuyển sang tab tiếp theo`, `next tab` | Cần Edge hoặc Chrome ở phía trước. |
| Tab trước | `tab trước`, `quay lại tab`, `chuyển về tab trước`, `lùi tab`, `previous tab` | Cần Edge hoặc Chrome ở phía trước. |
| Tab mới | `tab mới`, `mở tab mới`, `tạo tab mới`, `thêm tab`, `tab trống` | Cần Edge hoặc Chrome ở phía trước. |
| Đóng tab | `đóng tab`, `đóng tab này`, `tắt tab`, `close tab` | Cần Edge hoặc Chrome ở phía trước. Đóng tab đang hoạt động. |
| Cửa sổ mới | `mở cửa sổ mới`, `tạo cửa sổ mới`, `cửa sổ mới`, `mở cửa sổ trình duyệt mới`, `new window` | Cần Edge hoặc Chrome ở phía trước; Ctrl+N. |
| Cửa sổ ẩn danh | `mở cửa sổ ẩn danh`, `tạo cửa sổ ẩn danh`, `mở ẩn danh`, `mở cửa sổ riêng tư`, `mở inprivate`, `incognito` | Cần Edge hoặc Chrome ở phía trước; Ctrl+Shift+N mở InPrivate ở Edge hoặc Incognito ở Chrome. |

Cũng có thể nói câu hỏi trực tiếp. Các mẫu tìm kiếm tự nhiên gồm: "… bao nhiêu",
"… bao nhiêu tiền", "… là gì", "… là ai", "… ở đâu", "… khi nào", "… như thế nào",
"… thế nào", "… ra sao", "tại sao …", "vì sao …", "ai là …", "khi nào …", "ở đâu …",
"cách …", "làm thế nào …", "làm sao …", "giá …" và "thời tiết …". Cần có nội dung đi
kèm; nói riêng "bao nhiêu" hoặc "là gì" không mở tìm kiếm. Toàn bộ câu hỏi được gửi
đến Google sau khi bỏ lời đệm; kết quả và AI Overview (nếu có) hiện trong trình duyệt.
Lệnh rõ như "tìm …", "lưu thành …", "mở Word" vẫn được ưu tiên. Trong Đọc chính tả,
câu hỏi chỉ được chèn thành văn bản.

## 3. Windows

| Chức năng | Cách nói | Ghi chú |
|---|---|---|
| Mở ứng dụng | `mở word`, `mở excel`, `mở powerpoint`, `khởi động notepad`, `chạy máy tính`, `mở ứng dụng Word` | Chạy từ mọi cửa sổ. Tên ứng dụng phải có trong danh sách bên dưới. Chỉ nói "mở" sẽ được hỏi lại. |
| Chụp màn hình | `chụp màn hình`, `chụp ảnh màn hình`, `chụp màn` | Chạy từ mọi cửa sổ. Ảnh PNG được lưu vào `Pictures\VoicePilot`. |
| Đóng cửa sổ | `đóng cửa sổ`, `đóng cửa sổ này`, `tắt cửa sổ`, `close window` | Gửi Alt+F4 đến cửa sổ đang dùng. Chặn Desktop và taskbar để tránh hộp tắt máy. Nếu có nội dung chưa lưu, ứng dụng vẫn hỏi lưu; bạn tự chọn. |
| Bật tiết kiệm pin | `bật chế độ tiết kiệm pin`, `bật tiết kiệm pin`, `bật power saver` | Ghi nhớ power plan hiện tại rồi chuyển Windows sang **Power saver**. Bật lại khi đã bật giữ nguyên plan cũ. Nếu máy không hỗ trợ, app báo không khả dụng. |
| Tắt tiết kiệm pin | `tắt chế độ tiết kiệm pin`, `tắt tiết kiệm pin`, `tắt power saver`, `khôi phục chế độ nguồn` | Khôi phục đúng power plan trước khi VoicePilot bật Power saver, kể cả sau khi mở lại app. Nếu bạn đã tự đổi sang plan khác, app giữ lựa chọn đó. |
| Giảm độ sáng | `giảm độ sáng`, `giảm độ sáng màn hình` | Giảm 10 điểm phần trăm, giới hạn 0–100%; dành cho màn hình có hỗ trợ WMI, thường là màn hình laptop. |
| Tăng độ sáng | `tăng độ sáng`, `tăng độ sáng màn hình` | Tăng 10 điểm phần trăm; giá trị thực tế có thể được driver làm tròn. |
| Giảm âm lượng | `giảm âm lượng`, `giảm volume` | Giảm âm lượng Windows một nấc. |
| Tăng âm lượng | `tăng âm lượng`, `tăng volume` | Tăng âm lượng Windows một nấc. |
| Phát nhạc | `play`, `phát nhạc`, `tiếp tục phát nhạc`, `bật nhạc` | Gửi Play đến phiên media đang được Windows chọn. |
| Tạm dừng | `pause`, `tạm dừng nhạc`, `dừng nhạc`, `tạm dừng phát nhạc` | Gửi Pause riêng; không đảo trạng thái phát/tạm dừng. |
| Bài tiếp | `bài tiếp theo`, `bài tiếp`, `next`, `next bài` | Chuyển bài trong phiên media hiện tại. |
| Bài trước | `bài trước`, `previous`, `previous bài` | Quay lại bài trước trong phiên media hiện tại. |

Các thao tác nhạc dùng API phiên media Windows (SMTC). Cần app phát nhạc đăng ký
với Windows và hỗ trợ thao tác tương ứng; nếu không có phiên hoặc app từ chối,
VoicePilot báo rõ. Có thể dùng các hành động này trong **Lệnh tùy chỉnh**.

Power saver là power plan Windows. App lưu plan cũ trước khi đổi và chỉ xóa thông tin
đó sau khi khôi phục thành công. Nếu Power saver đã bật từ bên ngoài hoặc phiên app cũ
chưa lưu plan trước đó, app không đoán plan cũ: hãy chọn lại trong Power Options.

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


## Lệnh tùy chỉnh

Mở tray **Cài đặt → Lệnh tùy chỉnh → Thêm**. Nhập tên, cách gọi (mỗi dòng một cách),
chọn hành động và điền các trường hiện ra. Nhấn **Áp dụng lệnh vào danh sách**, rồi
**Lưu** ở Cài đặt. Bạn cũng có thể nhấn Lưu trực tiếp để lưu bản sửa đang nhập.
Không cần sửa code hay nhập API key.

| Cách gọi tự đặt | Hành động | Nội dung cấu hình |
|---|---|---|
| `hỏi google về {noidung}` hoặc `tra giá {noidung}` | Tìm Google | `{noidung}` |
| `xem vàng` | Tìm Google | `giá vàng hôm nay` |
| `tìm video {noidung}` | Mở website | `https://www.youtube.com/results?search_query={noidung}` |
| `mở trình soạn thảo` | Mở ứng dụng | `notepad.exe` hoặc chọn tệp `.exe` bằng nút Chọn ứng dụng |
| `chọn hết` | Nhấn tổ hợp phím | `ctrl+a` |
| `ký tên` | Chèn văn bản | Chữ ký, có thể nhập nhiều dòng và emoji |
| `cất bài {ten}` | PowerPoint: lưu tên khác | `{ten}` |

Tên tham số dùng chữ không dấu, số, `_`; bắt đầu bằng chữ. Trong mẫu gọi, đặt khoảng
trắng quanh `{thamso}`; tối đa 3 tham số khác nhau, có từ phân cách. Mọi cách gọi phải
cung cấp đủ tham số mà hành động sử dụng. Tối đa 20 cách gọi/lệnh và 200 lệnh.
Tên tệp/query/văn bản giữ tiếng Việt, chữ hoa và dấu câu. URL tự mã hóa tham số;
phần host phải cố định. Đường dẫn app và phím không được nhận từ lời nói.

**Ngữ cảnh** giới hạn nhóm cửa sổ; **Chỉ trong ứng dụng** nhận tên như `notepad.exe`
hoặc `winword.exe`. Các hành động PowerPoint/tab vẫn cần đúng ứng dụng như lệnh v1.
Tổ hợp phím và chèn văn bản cần cửa sổ đích; đổi focus hoặc hủy lượt sẽ chặn tác động.

**Kiểm tra câu nói** cho xem lệnh/tham số, không chạy thao tác; chọn ngữ cảnh và tên
exe giả định khi thử. Custom được ưu tiên trước lệnh có sẵn; mẫu có phần chữ cố định
cụ thể nhất thắng, rồi ưu tiên giới hạn ứng dụng/ngữ cảnh. Hai lệnh ngang nhau báo
xung đột. Dùng tên/mẫu khác hoặc tắt một lệnh. Nhân bản tạo bản sao đang tắt để sửa.
Trong Đọc chính tả, mọi cách gọi custom vẫn được chèn thành chữ.

**Nhập/Xuất** dùng JSON UTF-8 version 1. Nhập thêm vào danh sách, bỏ qua mục y hệt;
ID trùng nhưng khác nội dung được cấp ID mới. Tệp sai không thay thế danh sách hiện có.
Nhấn **Lưu** để dùng các thay đổi, **Hủy** để bỏ thay đổi. Cấu hình được giữ trong
`%LOCALAPPDATA%\VoicePilot\config.toml`, mục `[[custom_commands]]`.

Bộ ví dụ nhập được: `docs/custom-commands.example.json`. Harness:
`python tools/verify_custom_commands.py`; thêm `--windows` để kiểm fixture native.

## Chỉnh sửa prompt mặc định

Mở **Cài đặt → Prompt mặc định**, chọn lệnh ở bên trái và sửa các cách gọi, mỗi dòng
một cách. Danh sách bạn nhập thay thế cách gọi có sẵn của riêng lệnh đó. Hành động
và điều kiện ứng dụng vẫn giữ nguyên. Lệnh cần nội dung phải có ít nhất một mẫu nhận
tham số được hướng dẫn, ví dụ `hỏi web {query}` cho tìm kiếm hoặc `cất bài {filename}`
cho lưu thành. Không đổi tên tham số trong ngoặc. Chế độ chính tả vẫn chỉ chèn chữ.

**Khôi phục prompt của lệnh này** hoàn tác riêng lệnh đang chọn. Nhấn **Lưu** để áp
dụng; **Hủy** bỏ bản nháp. Prompt đã lưu có hiệu lực từ lượt tiếp theo, gồm hotwords
nhận dạng offline. Hướng dẫn trong app có mục **Prompt hiện tại** để xem cách gọi
đang có hiệu lực. Custom vẫn được ưu tiên trước các lệnh có sẵn.

## Khôi phục

Mở **Cài đặt → Khôi phục**:

| Tùy chọn | Phạm vi | Lệnh custom |
|---|---|---|
| Khôi phục bộ prompt mặc định | Hoàn tác chỉnh sửa cách gọi của mọi lệnh có sẵn. Cài đặt khác giữ nguyên. | Giữ nguyên. |
| Khôi phục toàn bộ setting | Cài đặt, phím tắt, vị trí thanh nổi, bí danh app/web và prompt có sẵn về mặc định. | Giữ nguyên. |
| Factory reset | Xóa toàn bộ dữ liệu trong thư mục dữ liệu riêng của VoicePilot: cấu hình, prompt/custom, lịch sử, log, bản thu debug, cache và báo cáo kiểm chứng. Khởi động lại với cài đặt gốc. | Xóa toàn bộ. |

Hai lựa chọn đầu chỉ sửa bản nháp: nhấn **Lưu** để áp dụng, **Hủy** để bỏ. Factory reset
có hộp xác nhận riêng và thực hiện ngay khi xác nhận; không thể hoàn tác. Nếu app đã
bật Power saver và còn lưu plan cũ, factory reset khôi phục plan đó trước khi xóa dữ
liệu. Bản cài đặt, model, tệp tài liệu và ảnh trong `Pictures\VoicePilot` được giữ.
Phím tắt gốc sau khôi phục setting/factory reset là **Ctrl + Alt + Space** và
**Ctrl + Alt + D**; bạn có thể sửa lại ở tab Chung.
