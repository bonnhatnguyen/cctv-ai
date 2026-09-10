# Hướng dẫn V1 — theo dõi người trong video MP4

## Phạm vi và riêng tư

V1 chỉ nhận **một tệp MP4 có sẵn trên máy**, phát hiện lớp COCO `person` bằng
YOLO26n và nối các phát hiện bằng ByteTrack. V1 không dùng camera trực tiếp,
RTSP, tải kho lưu trữ, nhận dạng khuôn mặt/danh tính, tư thế, bàn tay, vai trò,
tiền, hàng hóa, hành động, giao dịch, phát hiện sự kiện đáng ngờ, báo cáo hay
huấn luyện mô hình.

Máy chủ và trang web chỉ lắng nghe trên địa chỉ loopback `127.0.0.1`. Launcher
không tải model, không gửi video, ảnh, số đo hoặc telemetry ra ngoài máy, và
không tạo remote/push/publish. Không đổi `127.0.0.1` thành `0.0.0.0` nếu muốn
giữ đúng phạm vi riêng tư đã duyệt.

## Chuẩn bị một lần

- Python 3.12 của dự án phải nằm tại `.venv\Scripts\python.exe` (launcher có
  thể kiểm tra các fallback cục bộ đã cài, nhưng ưu tiên `.venv`).
- Chạy cài dependencies từ trước; `frontend\node_modules` phải có Vite và
  backend phải import được FastAPI, Uvicorn, Ultralytics, Torch, OpenCV và
  SQLAlchemy.
- `ffmpeg` và `ffprobe` phải có trên `PATH`.
- Đặt weights đã có sẵn tại `backend\weights\yolo26n.pt`. Launcher chạy
  offline và không tự tải weights.

## Khởi động

1. Nhấp đúp `start-v1.bat` trong thư mục gốc dự án.
2. Launcher kiểm tra runtime, packages, weights, FFmpeg/ffprobe, khởi động đúng
   `app.v1.api:app` và giao diện V1 trên loopback, đợi cả hai thật sự sẵn sàng,
   rồi mở trình duyệt.
3. Cổng thường là backend `8000` và giao diện `5173`. Nếu cổng thuộc tiến trình
   khác, launcher **không dừng hoặc tái sử dụng tiến trình đó**; nó chọn một
   cổng loopback an toàn trong dải nhỏ và in URL thực tế. Lần chạy lặp lại chỉ
   tái sử dụng dịch vụ có đúng service/version/instance của thư mục dự án này.
4. Log chẩn đoán và trạng thái PID nằm trong `data\v1-launcher\`. Khi lỗi, cửa
   sổ batch giữ lại thông báo dễ đọc.

`start-local.bat` vẫn dành cho ứng dụng cũ và không bị V1 thay thế.

## Xử lý video

1. Xuất đoạn cần xử lý từ đầu ghi thành MP4 trên máy. Phiên bản đầu tiên không
   nhận AVI/MKV/MOV, luồng trực tiếp hoặc URL. Video phải có frame timing cố
   định (CFR) và cả chiều rộng lẫn chiều cao phải là số chẵn; video VFR hoặc có
   kích thước lẻ bị từ chối rõ ràng ở bước đọc metadata.
2. Chọn **Chọn video MP4**, hoặc kéo/thả đúng một MP4 vào vùng chọn. Trang chỉ
   hiện tên và dung lượng cục bộ trước khi máy chủ xác nhận metadata.
3. Kiểm tra kích thước, thời lượng và codec mà máy chủ đọc được.
4. Chọn **Bắt đầu theo dõi người**. Các trạng thái thật gồm: chờ lượt, tải mô
   hình, theo dõi, mã hóa, kiểm tra đầu ra, hoàn tất hoặc thất bại. Phần trăm
   trong giai đoạn theo dõi chỉ là ước tính và không lên 100% trước khi đầu ra
   được kiểm tra.
5. Khi hoàn tất, phát/tua riêng **Video gốc** và **Video đã theo dõi**. Chọn
   **Tải video kết quả** để lưu MP4 có khung và nhãn. Video kết quả là H.264
   không có track âm thanh (silent annotated output); V1 không sao chép âm
   thanh từ tệp nguồn.

Tệp nguồn được giữ nguyên trong vùng dữ liệu riêng của job, kể cả khi xử lý
thất bại hoặc bị gián đoạn. Không xóa `data\v1` khi còn cần nguồn, kết quả hoặc
lịch sử job.

## Ý nghĩa và giới hạn của ID

Nhãn `người #ID` chỉ là ID cục bộ trong **một clip**. Nó không phải tên hoặc
danh tính thật, không nối qua hai video khác nhau và có thể đổi khi người bị che
khuất lâu, ra khỏi khung rồi quay lại hoặc tracker mất dấu. Một người có thể có
nhiều ID trong clip; không dùng ID này để kết luận danh tính hay hành vi.

## Khởi động lại và dừng V1

- Nhấp đúp launcher lần nữa là an toàn: chỉ các dịch vụ đã xác minh đúng mới
  được tái sử dụng.
- Để dừng các tiến trình do launcher này sở hữu, chạy từ thư mục gốc:

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-v1.ps1 -Stop
  ```

  Lệnh kiểm tra state, instance, service identity, thời điểm tạo process,
  executable, command line chính xác và quan hệ với listener trước khi dừng;
  nó từ chối dừng PID không khớp hoặc PID đã được process khác tái sử dụng.
- Nếu máy bị tắt hoặc backend bị kết thúc khi job đang chạy, lần khởi động sau
  đánh dấu job đó `xu_ly_bi_gian_doan` thay vì báo hoàn tất giả. Tệp nguồn vẫn
  còn để người vận hành chọn/xử lý lại.

## Thông báo giấy phép Ultralytics

Ultralytics YOLO26, framework và weights mặc định được cung cấp theo giấy phép
**AGPL-3.0**. Việc dùng/phân phối/triển khai phải tuân thủ AGPL-3.0; trước khi
triển khai thương mại theo điều khoản không tương thích, cần có giấy phép
Ultralytics Enterprise phù hợp. Đây không phải tư vấn pháp lý; người triển khai
phải tự xác nhận nghĩa vụ giấy phép áp dụng.
