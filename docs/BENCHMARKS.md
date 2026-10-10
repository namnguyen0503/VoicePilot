# Benchmark STT và bộ phân tích lệnh

Tài liệu này mô tả cách đo độ chính xác, độ trễ và bộ nhớ của ba chế độ nhận dạng (Google, Gipformer offline, Hybrid) và bộ phân tích lệnh. Mục 6 chờ giọng nói thật; mục 7 giữ số đo tổng hợp để chẩn đoán.

Yêu cầu của kế hoạch: ít nhất **30 câu**, đo trên laptop mục tiêu (Intel i3-1005G1, 16 GB RAM, Windows 10), ghi độ chính xác intent, độ trễ p50 và p95 và RAM. Mục tiêu p95 từ lúc ngừng nói đến khi có phản hồi cho lệnh ngắn là **không quá 4 giây**. Công cụ này chỉ đo phần STT và phân tích lệnh. Thời gian đến khi có phản hồi trên màn hình cần đo riêng khi quay video demo.

## 1. Chuẩn bị bộ dữ liệu

1. Thu 30 câu theo kịch bản ở mục 2, mỗi câu một tệp WAV. Mic và phòng giống lúc dùng thật. Không cần chuẩn hóa tần số lấy mẫu; công cụ chuyển về 16 kHz mono.
2. Đặt các tệp WAV và `labels.csv` trong một thư mục, ví dụ `bench\session-01\`. Thư mục này không đưa vào Git.
3. Chép `docs/benchmark-labels.csv` thành `labels.csv` (30 câu ở mục 2), rồi sửa nhãn nếu bạn đọc khác.

## 2. Kịch bản 30 câu tiếng Việt

30 câu bao phủ đủ 15 chức năng trong `docs/COMMANDS.vi.md`, trong đó có hai câu đọc chính tả. Đọc tự nhiên, không nhấn mạnh.

| # | Câu cần đọc | Chức năng | Ngữ cảnh | Tham số mong đợi |
|---|---|---|---|---|
| 1 | lưu lại | Lưu (`PPT_SAVE`) | powerpoint | `{}` |
| 2 | lưu thành báo cáo tháng chín | Lưu tên khác (`PPT_SAVE_AS`) | powerpoint | `{"filename": "báo cáo tháng chín"}` |
| 3 | slide tiếp theo | Slide tiếp (`PPT_NEXT_SLIDE`) | powerpoint | `{}` |
| 4 | slide trước | Slide trước (`PPT_PREV_SLIDE`) | powerpoint | `{}` |
| 5 | bắt đầu trình chiếu | Bắt đầu trình chiếu (`PPT_START_SHOW`) | powerpoint | `{}` |
| 6 | dừng trình chiếu | Dừng trình chiếu (`PPT_STOP_SHOW`) | powerpoint | `{}` |
| 7 | mở youtube | Mở website (`BROWSER_OPEN_SITE`) | browser | `{"url": "https://www.youtube.com"}` |
| 8 | mở facebook | Mở website (`BROWSER_OPEN_SITE`) | browser | `{"url": "https://www.facebook.com"}` |
| 9 | tìm thời tiết hôm nay | Tìm Google (`BROWSER_SEARCH`) | browser | `{"query": "thời tiết hôm nay"}` |
| 10 | tìm kiếm giá vàng hôm nay | Tìm Google (`BROWSER_SEARCH`) | browser | `{"query": "giá vàng hôm nay"}` |
| 11 | tab tiếp theo | Tab tiếp (`BROWSER_NEXT_TAB`) | browser | `{}` |
| 12 | tab tiếp | Tab tiếp (`BROWSER_NEXT_TAB`) | browser | `{}` |
| 13 | tab trước | Tab trước (`BROWSER_PREV_TAB`) | browser | `{}` |
| 14 | chuyển về tab trước | Tab trước (`BROWSER_PREV_TAB`) | browser | `{}` |
| 15 | tab mới | Tab mới (`BROWSER_NEW_TAB`) | browser | `{}` |
| 16 | mở tab mới | Tab mới (`BROWSER_NEW_TAB`) | browser | `{}` |
| 17 | đóng tab | Đóng tab (`BROWSER_CLOSE_TAB`) | browser | `{}` |
| 18 | đóng tab này | Đóng tab (`BROWSER_CLOSE_TAB`) | browser | `{}` |
| 19 | mở word | Mở ứng dụng (`WIN_OPEN_APP`) | other | `{"app": "WINWORD.EXE", "app_name": "word"}` |
| 20 | mở notepad | Mở ứng dụng (`WIN_OPEN_APP`) | other | `{"app": "notepad.exe", "app_name": "notepad"}` |
| 21 | chụp màn hình | Chụp màn hình (`WIN_SCREENSHOT`) | other | `{}` |
| 22 | chụp ảnh màn hình | Chụp màn hình (`WIN_SCREENSHOT`) | other | `{}` |
| 23 | xin chào, tôi đang kiểm thử chức năng đọc chính tả | Đọc chính tả (`DICTATE_TEXT`) | other | `{}` |
| 24 | cuộc họp sẽ bắt đầu lúc chín giờ sáng | Đọc chính tả (`DICTATE_TEXT`) | other | `{}` |
| 25 | mở powerpoint | Mở ứng dụng (`WIN_OPEN_APP`) | other | `{"app": "POWERPNT.EXE", "app_name": "powerpoint"}` |
| 26 | mở máy tính | Mở ứng dụng (`WIN_OPEN_APP`) | other | `{"app": "calc.exe", "app_name": "máy tính"}` |
| 27 | slide tiếp | Slide tiếp (`PPT_NEXT_SLIDE`) | powerpoint | `{}` |
| 28 | trang trước | Slide trước (`PPT_PREV_SLIDE`) | powerpoint | `{}` |
| 29 | kết thúc trình chiếu | Dừng trình chiếu (`PPT_STOP_SHOW`) | powerpoint | `{}` |
| 30 | mở google | Mở website (`BROWSER_OPEN_SITE`) | browser | `{"url": "https://www.google.com"}` |

Mẹo: các câu 19, 20, 25 và 26 có tham số `app`. Tham số này lấy từ bảng `[apps]` trong `config.toml`. Nếu bạn đổi bảng alias, hãy cập nhật cột tương ứng trong `labels.csv`.

## 3. Định dạng `labels.csv`

```csv
file,expected_intent,expected_params_json,context,reference
s01.wav,PPT_SAVE,{},powerpoint,lưu lại
s02.wav,PPT_SAVE_AS,"{""filename"": ""báo cáo tháng chín""}",powerpoint,lưu thành báo cáo tháng chín
s09.wav,BROWSER_SEARCH,"{""query"": ""thời tiết hôm nay""}",browser,tìm thời tiết hôm nay
s23.wav,DICTATE_TEXT,{},other,xin chào tôi đang kiểm thử chức năng đọc chính tả
```

| Cột | Bắt buộc | Ý nghĩa |
|---|---|---|
| `file` | Có | Tên tệp WAV trong cùng thư mục. |
| `expected_intent` | Có | Tên `Intent` trong `contracts.py`. |
| `expected_params_json` | Có | Đối tượng JSON. Mỗi cặp khóa-giá trị phải khớp (so sánh sau khi chuẩn hóa chữ thường và khoảng trắng). `{}` nghĩa là không kiểm tra tham số. |
| `context` | Có | `powerpoint`, `browser`, `other` hoặc `none`. |
| `reference` | Không | Câu đọc nguyên văn. Có cột này thì tính được WER và tỷ lệ khớp chính xác. |

Ghi chú: cột `reference` không có trong đặc tả ban đầu nhưng là cần thiết để tính WER. Nếu bỏ cột này, công cụ vẫn chạy và ghi "n/a" cho WER và tỷ lệ khớp chính xác.

## 4. Cách chạy

```powershell
# Cài môi trường (một lần): xem README, mục "Quick start".
# Chạy từng backend trong một lần gọi riêng để đo bộ nhớ riêng cho từng backend.
.\.venv\Scripts\python.exe tools\benchmark_stt.py --input bench\session-01 --backends google --output-md bench\google.md --output-json bench\google.json
.\.venv\Scripts\python.exe tools\benchmark_stt.py --input bench\session-01 --backends offline --output-md bench\offline.md --output-json bench\offline.json
.\.venv\Scripts\python.exe tools\benchmark_stt.py --input bench\session-01 --backends hybrid --output-md bench\hybrid.md --output-json bench\hybrid.json
```

Tùy chọn quan trọng:

- `--model-dir`: thư mục Gipformer. Mặc định là `models\asr\gipformer1.5-int8`.
- `--threads`: số luồng CPU của Gipformer. Mặc định 2, khớp với cấu hình ứng dụng.
- `--google-timeout`: số giây chờ Google trước khi chạy Gipformer trong chế độ hybrid. Mặc định 2.0.
- Cài `psutil` (`pip install -e .[benchmark]`) để có cột RAM đỉnh. Không có `psutil` thì cột này ghi "n/a".

Công cụ dùng cùng `HybridTranscriber` và `RuleParser` như ứng dụng. Với `--backends google`, Gipformer không được tải. Với `--backends offline`, Google không được gọi.

Model lệnh dùng hotwords từ cấu hình như app; đọc chính tả chọn decoder riêng không có hotwords.
Validator dùng chung với controller: ưu tiên lệnh phù hợp app đích (slide cần PowerPoint,
tab cần browser) và URL nằm trong bảng website. Nếu cả hai kết quả không hợp lệ, hybrid vẫn
trả văn bản tốt nhất để app giải thích điều kiện thiếu. Lần đầu đọc chính tả offline có thể
nạp thêm decoder; thời gian này nằm trong độ trễ của câu đó. Bộ luồng được đóng sau mỗi backend.

Đầu ra JSON không chứa nội dung nhận dạng, chỉ có độ trễ, nguồn, trạng thái và số lỗi từng câu.

## 5. Định nghĩa chỉ số

| Chỉ số | Cách tính |
|---|---|
| Không có kết quả | Số câu mà backend không trả về văn bản hợp lệ (lỗi mạng, lỗi model, hoặc `SttError`). |
| Độ chính xác intent | Số câu có intent đúng sau khi phân tích / tổng số câu. Câu không có kết quả tính là sai. |
| Độ chính xác tham số | Số câu có intent đúng và mọi tham số mong đợi khớp / tổng số câu. |
| WER | Tổng số lần sửa (thêm, xóa, thay từ) / tổng số từ trong câu tham chiếu, tính trên toàn bộ tập. Chuẩn hóa: chữ thường, bỏ dấu câu, giữ nguyên dấu tiếng Việt. Câu không có kết quả tính là xóa hết mọi từ. |
| Khớp chính xác | Tỷ lệ câu mà văn bản chuẩn hóa trùng khớp hoàn toàn với câu tham chiếu. |
| p50 / p95 độ trễ | Phân vị theo phương pháp nearest-rank trên thời gian gọi `transcribe()`, gồm cả các lần lỗi. Không gồm thời gian thu âm và không gồm thời gian vẽ giao diện. |
| Thời gian nạp model | Thời gian `warmup()` của Gipformer (một lần cho mỗi lần chạy). |
| RAM đỉnh | Working set đỉnh của tiến trình (Windows) sau khi chạy backend. Chạy riêng từng backend để số này không bị cộng dồn. |

## 6. Mẫu kết quả

Ngày 2026-10-10 đã chạy smoke bằng giọng tổng hợp: ASR/TTS cùng tiến trình và decoder đọc chính tả
đều hoạt động trên Windows. Đây chưa phải benchmark 30 câu nói thật. Phần đo micro và desktop
được Nam để lại sau; bảng dưới vẫn chờ dữ liệu thật.

Điền kết quả sau mỗi lần đo. Ghi rõ commit, ngày và cấu hình máy.

**Môi trường**

| Mục | Giá trị |
|---|---|
| Ngày đo | |
| Commit VoicePilot | |
| Máy | Intel i3-1005G1, 16 GB RAM (xác nhận) |
| Windows | |
| Micro và phòng | |
| Kết nối mạng khi đo Google | |
| Số câu | |
| Số luồng Gipformer | 2 |

**Kết quả**

| Backend | Số câu | Không có kết quả | Độ chính xác intent | Độ chính xác tham số | WER | Khớp chính xác | p50 (ms) | p95 (ms) | Nạp model (ms) | RAM đỉnh (MB) | Nguồn kết quả |
|---|---|---|---|---|---|---|---|---|---|---|---|
| google | | | | | | | | | - | | |
| offline | | | | | | | | | | | |
| hybrid | | | | | | | | | | | |

**Ghi chú phân tích**

- Câu sai nhiều nhất và nguyên nhân:
- Chức năng có độ trễ p95 cao nhất:
- Hybrid có lấy kết quả Gipformer ở câu nào không (cột "Nguồn kết quả"):
- Có vượt mục tiêu p95 4 giây không:

## 7. Bộ âm thanh tổng hợp để chẩn đoán (2026-10-10)

Đã tự sinh 30 WAV từ câu tham chiếu bằng Piper, không thu micro/phát loa, rồi gọi CLI
benchmark riêng cho từng backend trên i3-1005G1/Windows 10. Mã lúc đo là `de4085f`,
trước bản sửa chọn STT theo app đích. Giọng tổng hợp này không phải giọng Nam và không
được dùng để đánh dấu hoàn thành benchmark thật hay demo v1.

| Backend | Câu | Không có kết quả | Intent đúng | Tham số đúng | WER | p50 (ms) | p95 (ms) | Nạp model (ms) | RAM đỉnh (MB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Google | 30 | 18 | 16.7% | 10.0% | 58.8% | 318.8 | 808.9 | — | 146.2 |
| Offline | 30 | 0 | 36.7% | 36.7% | 32.4% | 40.4 | 70.1 | 2181.5 | 404.4 |
| Hybrid | 30 | 0 | 36.7% | 33.3% | 36.3% | 396.0 | 3942.0 | 2193.5 | 371.3 |

Các câu ngắn có tên tiếng Anh như slide/tab/Word dễ sai; nhiều WAV dưới một giây.
Thêm khoảng lặng đầu/cuối chỉ tăng intent offline từ 11/30 lên 12/30; sinh lại có
dấu chấm cuối vẫn 11/30. Greedy không hotwords đạt 10/30 trên bộ có dấu chấm.
Chưa có cơ sở đổi alias theo những câu nhận nhầm này hoặc kết luận độ chính xác giọng thật.
Số p95 chỉ gồm STT/parser, không phải ngừng nói → phản hồi toàn app.

WAV, nhãn, JSON/Markdown, stdout và chẩn đoán giữ ở thư mục bị Git ignore:
`build/synthetic-corpus-5733e942ee924b5e9718553326215e8a/`.
