# VoicePilot v1 — Trợ lý giọng nói cho Windows

Trạng thái: đã triển khai 15 chức năng; kiểm chứng tác động desktop đạt ngày 2026-10-11.
Benchmark và demo giọng nói thật còn chờ; xem HANDOFF/TODO.

Vị trí repo cố định: `C:\Users\Nam\Documents\GitHub\VoicePilot`.

## 1. Mục tiêu và trải nghiệm

Xây dựng bản demo trong vài ngày, chạy trên laptop i3-1005G1/16 GB RAM, có **15 chức năng thực tế**, repo độc lập để đưa vào CV và ZIP portable kèm model.

Luồng sử dụng:

**Phím tắt → nói tiếng Việt → nhận dạng → hiểu lệnh theo cửa sổ đang dùng → thực hiện → phản hồi bằng chữ và giọng nói.**

| Nhóm | Chức năng v1 | Số lượng |
|---|---|---:|
| PowerPoint | Lưu; lưu tên khác; slide tiếp; slide trước; bắt đầu trình chiếu; dừng trình chiếu | 6 |
| Edge/Chrome | Mở website; tìm Google; tab tiếp; tab trước; tab mới; đóng tab | 6 |
| Windows | Mở ứng dụng; chụp màn hình | 2 |
| Đọc chính tả | Chèn lời nói thành văn bản tại con trỏ | 1 |

Giao diện dùng thanh nổi khoảng 520 px, nền tối, điểm nhấn xanh, đặt giữa phía dưới màn hình và kéo được. Thanh hiện chế độ, ứng dụng đích, mức âm lượng, trạng thái và câu nhận dạng. Tray có menu mở thanh, cài đặt, danh sách lệnh và thoát.

Mặc định:

- `Ctrl + Alt + Space`: bắt đầu nghe; nhấn lại để kết thúc sớm.
- Tự kết thúc sau khoảng 800 ms im lặng.
- `Ctrl + Alt + D`: đổi giữa **Ra lệnh** và **Đọc chính tả**.
- Có nút hủy lượt đang xử lý; phím tắt được đổi trong cài đặt.
- Mỗi lệnh nhận một câu phản hồi ngắn. Đọc chính tả chỉ báo trạng thái.
- Thanh nổi không lấy focus khỏi ứng dụng đích.

## 2. Thiết kế triển khai

**Nền tảng:** Python 3.12 x64 trong `.venv`, PySide6 Widgets qua `PySide6-Essentials`, SoundDevice, WebRTC VAD, SpeechRecognition, sherpa-onnx, Piper, pywin32 và mss. Khóa dependency trong `requirements.lock`.

### Audio và STT

- Thu âm ở sample rate thiết bị hỗ trợ; chuẩn hóa thành mono 16 kHz.
- Giới hạn mỗi lượt: 15 giây cho lệnh, 30 giây cho chính tả; hết 5 giây chưa có tiếng nói thì kết thúc.
- Google nhận dạng với `vi-VN`. Khi lỗi hoặc chưa có kết quả hợp lệ sau 2 giây, chạy Gipformer offline trên cùng đoạn audio.
- Dùng Gipformer 1.5 68M INT8 qua sherpa-onnx, CPU, mặc định 2 thread và greedy decoding. Tác giả cung cấp cách chạy ONNX và các tệp INT8. [Hướng dẫn chính thức](https://github.com/ggroup-ai-lab/gipformer).
- Trong chế độ lệnh, kết quả hợp lệ phải khớp một intent và đủ tham số. Trong chính tả, kết quả phải có văn bản.
- Mỗi lượt có ID và chỉ được thực hiện một lần; bỏ kết quả đến muộn sau khi đã chọn kết quả hoặc hủy lượt.
- Cài đặt cho phép chọn Hybrid, Google hoặc Offline để sử dụng và kiểm thử.

### Hiểu lệnh và thực thi

- Một lượt nói thực hiện một lệnh; mỗi lệnh hỗ trợ nhiều cách nói tiếng Việt.
- Dùng luật và mẫu trong `commands.toml`. Chuẩn hóa để so khớp nhưng giữ nguyên nội dung tham số như tên tệp, câu tìm kiếm và văn bản.
- Chụp ngữ cảnh cửa sổ trước khi hiện thanh nổi. Kiểm tra lại cửa sổ và focus trước khi thực thi; nếu đích đã đổi thì giữ câu nhận dạng và báo cần thử lại.
- Lệnh không rõ hoặc thiếu tham số sẽ hiện một ví dụ nói lại.
- Chính tả chèn Unicode qua Windows `SendInput`; câu nói trong chế độ này luôn được xử lý như nội dung văn bản. [API Windows](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).

### PowerPoint

- Dùng COM trên một worker riêng có khởi tạo COM; đối tượng COM được giữ trong worker đó.
- Chuyển slide hoạt động trong cả chế độ chỉnh sửa và trình chiếu; đầu/cuối thì thông báo.
- Bắt đầu trình chiếu từ slide đầu; dừng trả về chế độ chỉnh sửa.
- Lưu dùng tệp hiện tại. Nếu tệp chưa có đường dẫn, hướng dẫn nói “lưu thành tên tệp”.
- Lưu tên khác cạnh tệp hiện tại; tệp chưa từng lưu dùng `Documents\VoicePilot`.
- Giữ tên tiếng Việt, dùng `.pptx`; trùng tên tự thêm `(2)`, `(3)…`. Tên không hợp lệ thì yêu cầu nói lại. [API SaveAs](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentation.saveas).

### Trình duyệt và Windows

- Điều khiển Edge/Chrome đang dùng bằng thao tác Windows, kiểm tra đúng cửa sổ trước khi gửi phím.
- Mở website và tìm Google trong tab mới. Khi chưa có trình duyệt đích, dùng trình duyệt mặc định.
  Tìm kiếm nhận cả câu hỏi tự nhiên như "giá vàng hôm nay bao nhiêu", "Python là gì",
  "thời tiết Hà Nội hôm nay"; giữ toàn bộ câu hỏi làm query. Lệnh rõ được ưu tiên trước.
  Đây là mở kết quả Google; không lấy giá/đọc AI Overview trong app.
- Website nhận tên alias cấu hình sẵn hoặc địa chỉ HTTP/HTTPS.
- Mở ứng dụng qua danh sách alias: PowerPoint, Word, Excel, Edge, Chrome, Notepad và Calculator; chỉ chức năng mở ứng dụng được tính trong v1.
- Chụp màn hình chứa cửa sổ đích, ẩn thanh nổi lúc chụp, lưu PNG có timestamp vào `Pictures\VoicePilot`.

### TTS, dữ liệu và giao diện lập trình

- Piper offline dùng giọng `vi_VN-vais1000-medium`, phát bằng SoundDevice; dừng phát khi bắt đầu lượt nghe mới.
- GUI chạy trên main thread; audio, STT, TTS và thao tác ứng dụng chạy qua worker và signal.
- Các interface nội bộ: `transcribe(AudioClip) → Transcript`, `parse(text, context) → Command`, `execute(command, context) → ActionResult`, `speak(text)`.
- Entry point phát triển: `python -m voicepilot`; bản portable: `VoicePilot.exe`.
- Cấu hình lưu tại `%LOCALAPPDATA%\VoicePilot\config.toml`. Giữ 20 câu gần nhất trong RAM; log đĩa chỉ chứa độ trễ, trạng thái và mã lỗi.
- Repo dùng GPL-3.0-or-later; lưu nguồn và giấy phép runtime/model trong thông tin bên thứ ba. [Giấy phép Piper](https://pypi.org/project/piper-tts/).

## 3. Cây thư mục dự kiến

```text
VoicePilot/
├── .github/
│   └── workflows/
│       └── ci.yml
├── .gitignore
├── .python-version
├── pyproject.toml
├── requirements.lock
├── README.md
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── docs/
│   ├── PLAN.md
│   ├── COMMANDS.vi.md
│   ├── DEMO.vi.md
│   ├── BENCHMARKS.md
│   └── TODO.md
├── src/
│   └── voicepilot/
│       ├── __init__.py
│       ├── __main__.py
│       ├── app.py
│       ├── config.py
│       ├── contracts.py
│       ├── controller.py
│       ├── audio/
│       │   ├── capture.py
│       │   └── vad.py
│       ├── stt/
│       │   ├── google.py
│       │   ├── gipformer.py
│       │   └── hybrid.py
│       ├── tts/
│       │   └── piper.py
│       ├── intent/
│       │   ├── normalize.py
│       │   └── parser.py
│       ├── windows/
│       │   ├── context.py
│       │   ├── hotkeys.py
│       │   ├── input.py
│       │   ├── powerpoint.py
│       │   ├── browser.py
│       │   └── desktop.py
│       ├── ui/
│       │   ├── overlay.py
│       │   ├── tray.py
│       │   └── settings.py
│       └── resources/
│           ├── defaults.toml
│           ├── commands.toml
│           ├── models.toml
│           ├── style.qss
│           └── icon.svg
├── tests/
│   ├── fixtures/
│   │   └── commands.json
│   └── unit/
│       ├── test_parser.py
│       ├── test_hybrid.py
│       ├── test_controller.py
│       └── test_windows.py
├── tools/
│   ├── download_models.py
│   ├── benchmark_stt.py
│   └── build.ps1
├── packaging/
│   └── voicepilot.spec
└── models/                     # Binary được Git ignore
    ├── asr/
    │   └── gipformer1.5-int8/
    └── tts/
        └── vais1000-medium/
```

Các package con có `__init__.py`. Manifest model được theo dõi trong Git, ghi revision và checksum; model thực tế đi cùng bản ZIP.

## 4. Thứ tự làm và kiểm chứng

| Mốc | Công việc | Điều kiện hoàn thành |
|---|---|---|
| Ngày 1 | Môi trường, STT/TTS, parser và backend Windows | Thu → nhận dạng → đọc phản hồi chạy được; bộ lệnh được kiểm thử riêng bằng văn bản |
| Ngày 2 | Thanh nổi, hotkey, hybrid và chính tả | Toàn bộ luồng giọng nói hoạt động; chuyển cửa sổ không gây thao tác sai |
| Ngày 3 | Kiểm thử, sửa lỗi, đóng gói, tài liệu và video | ZIP chạy độc lập, có bảng demo 15 chức năng và số đo benchmark |

Kiểm thử bắt buộc:

- **Parser:** nhiều cách nói, tiếng Việt có dấu, tên tệp/query giữ đúng nội dung, thiếu tham số, câu không phải lệnh.
- **Hybrid:** Google nhanh/chậm/lỗi, offline lỗi, hủy lượt, kết quả đến muộn và hai backend cùng trả kết quả.
- **Windows:** PowerPoint chưa mở, nhiều tài liệu, slide đầu/cuối, tệp chưa lưu/trùng tên; Chrome và Edge; đổi focus giữa lượt.
- **Audio/UI:** micro không khả dụng, im lặng, nhiễu nhẹ, lượt dài, TTS không bị thu lại, DPI 100–150%.
- **Portable:** chạy từ thư mục có dấu và khoảng trắng, không cần Python, model đọc được khi mất mạng, các DLL COM/ONNX/Qt được đóng gói đầy đủ.

Benchmark ít nhất 30 câu trên laptop, ghi độ chính xác intent, độ trễ p50/p95 và RAM. Mục tiêu p95 từ lúc ngừng nói đến phản hồi không quá 4 giây cho câu lệnh ngắn.

Trước khi phát hành, demo đủ 15 chức năng; chọn 10 chức năng ổn định nhất chạy lại ba vòng. CI Windows chạy unit test với backend giả lập; kiểm thử micro và Office thực hiện trên máy demo.

## 5. Giả định và TODO

- Lịch ba ngày là khung triển khai ban đầu theo yêu cầu “trong vài ngày”.
- Máy kiểm thử chính là Windows 10 LTSC hiện tại, PowerPoint desktop và tài liệu `.pptx` chỉnh sửa được.
- README chính bằng tiếng Anh; giao diện, hướng dẫn lệnh và kịch bản demo bằng tiếng Việt.
- Bản phát hành dùng PyInstaller **onedir**, nén ZIP cùng model, tài liệu và thông tin source tương ứng. [Cách đóng gói](https://pyinstaller.org/en/stable/operating-mode.html).

TODO sau v1: **Linux**, thao tác Word/Excel, tạo và sửa slide qua COM, giá vàng trực tiếp, Playwright/extension, nhiều lệnh trong một câu, từ khóa đánh thức và benchmark mô hình STT khác trên PC.


## 6. Lệnh tùy chỉnh (Nam bổ sung phạm vi ngày 2026-10-11)

- Cài đặt có tab Lệnh tùy chỉnh: thêm/sửa/xóa/nhân bản/bật-tắt, nhập/xuất JSON version 1.
- Mỗi lệnh có tên, nhiều mẫu gọi, một hành động, tham số cố định hoặc lấy từ lời nói,
  ngữ cảnh và tùy chọn tên exe đích. Hỗ trợ 15 hành động v1 và tổ hợp phím Windows.
- Ví dụ: `hỏi google về {noidung}` → tìm Google với query `{noidung}`.
  URL mã hóa phần tham số; đường dẫn app và tổ hợp phím phải được cấu hình cố định.
- Custom được ưu tiên trước luật có sẵn, mẫu cụ thể nhất thắng; hòa giữa các lệnh khác
  nhau báo xung đột, không chọn ngẫu nhiên. Chính tả vẫn chỉ chèn toàn bộ lời nói.
- Kiểm tra câu nói chỉ xem intent/params, không thực thi. Lưu áp dụng parser/hotwords
  vào lượt tiếp theo; lượt đang nghe giữ snapshot cũ. Một lượt thực hiện một hành động.
- Harness acceptance được tạo/chạy trước code; kiểm tra CRUD, lưu/nạp, nhập/xuất,
  16 hành động qua bốn đường STT, focus/cancel và tác động native trên fixture riêng.


## Bổ sung theo yêu cầu Nam ngày 2026-10-11

- Hai tab Voice/Text; Enter/nút gửi dùng cùng pipeline, tắt thu âm trong Text.
- Đổi Ra lệnh/Chính tả khi đang ghi giữ PCM, dùng chế độ mới cho cả lượt;
  khi đã nhận dạng/thực thi thì hủy lượt cũ.
- Cài đặt tab khi mở lại: Voice mặc định, nhớ lựa chọn cuối hoặc luôn mở Text.
- Hướng dẫn sử dụng trong cửa sổ riêng, có danh mục/tìm kiếm/phím tắt thực tế.
- Chín hành động Windows mới: Power saver (Nam chốt chuyển power plan), độ sáng
  tăng/giảm 10 điểm phần trăm, âm lượng tăng/giảm, media Play/Pause/Next/Previous qua SMTC.
- Các hành động mới cũng có trong trình tạo lệnh custom. Wi-Fi/Excel vẫn ngoài phạm vi.
