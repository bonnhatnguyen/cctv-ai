# Hướng dẫn V2 M1 — khoanh ROI rổ tiền

V2 M1 chạy cục bộ trên máy và chỉ thêm công cụ mở clip, xem đúng khung hình
nguồn, rồi khoanh/lưu vùng rổ tiền cố định. M1 **chưa gán nhãn hành động** và
không tự suy luận `hand_in`, `hand_out`, lấy tiền hay bỏ tiền.

## Mở clip

1. Khởi động bằng `start-v1.bat` như V1.
2. Nhập một MP4 hợp lệ. Có thể bấm **Khoanh rổ tiền** ngay; không cần chạy
   person tracking.
3. Hoặc bấm **Danh sách clip** để mở lại clip đã đăng ký trước đó. Khi trạng
   thái còn `preparing`, chờ bản preview sạch và chỉ mục frame lossless được
   chuẩn bị xong.

Video nguồn vẫn nằm trong thư mục job UUID riêng tư. Dữ liệu annotation,
preview sạch và chunks lossless nằm trong private annotation root; API/UI
không công bố đường dẫn filesystem.

## Chọn đúng frame và vẽ ROI

- **Phát preview** dùng video H.264 sạch để xem nhanh. Chọn tốc độ 0,25×, 0,5×
  hoặc 1×. Khi tạm dừng, ứng dụng đổi thời gian preview sang frame gần nhất và
  tải lại ảnh lossless của đúng frame đó trước khi cho sửa ROI.
- Thanh trượt hoặc ô **Frame** chọn frame zero-based. Khi đang dừng, phím
  Left/Right lùi/tiến một frame; Space phát/tạm dừng.
- Tạo hoặc chọn một camera. Bấm trên ảnh để đặt ít nhất ba đỉnh; kéo một đỉnh
  để chỉnh, Backspace bỏ đỉnh cuối, rồi bấm **Đóng vùng**.
- **Lưu ROI** chỉ bật khi ảnh chính xác hiện tại đã tải xong và polygon hợp lệ.
  Ctrl+S lưu; Escape hủy bản nháp về revision đã lưu.

ROI dùng tọa độ chuẩn hóa trên đúng vùng ảnh sau khi áp dụng sample aspect
ratio (SAR), nên không bị lệch khi cửa sổ hoặc độ phân giải hiển thị thay đổi.

## Xem lại ROI và kết quả tracking đúng clip

Trong workspace, **Phát preview** phát video của clip đang chọn từ frame hiện
tại và hiển thị ROI đã lưu của chính clip đó. Khi phát, ROI chỉ để xem; bản
vẽ chưa lưu không thay thế ROI đã lưu. Chọn frame bằng thanh trượt hoặc ô Frame
sẽ dừng preview và tải ảnh chính xác để tiếp tục chỉnh sửa.

**Xem tracking của clip này** mở kết quả ngay bên dưới, dùng liên kết job nguồn
của clip đang chọn. Nếu chưa chạy tracking, ứng dụng báo rõ; không lấy kết quả
của video khác. Thao tác này không tự chạy tracking hoặc đổi video đang mở ở
V1. **Quay lại theo dõi** giữ nguyên video V1 trước đó.

ROI được phủ trên hai player của kết quả liên kết; lớp phủ này chỉ hiển thị
trên trang, không được ghi vào MP4 tải xuống hoặc fullscreen riêng của video.
Mỗi clip giữ ROI riêng; tên file/cùng camera không tự gắn ROI sang clip khác.

## Mẫu camera và revision

**Lưu làm mẫu** tạo một revision mẫu riêng cho camera. Khi dùng mẫu, bấm
**Xem mẫu camera**, kiểm tra polygon trên clip hiện tại, rồi bấm **Xác nhận ROI
cho clip này** trước khi lưu. Sửa mẫu về sau không tự đổi ROI đã chốt của clip
cũ.

Nếu hai tab cùng sửa một clip, tab lưu với revision cũ sẽ báo xung đột và giữ
nguyên bản vẽ để người dùng tải lại/đối chiếu; ứng dụng không ghi đè im lặng.
Rời workspace hoặc đổi clip khi còn bản nháp sẽ có cảnh báo.

## Dung lượng và phục hồi

**Giải phóng bản xem tạm** chỉ xóa preview/chunks/cache phát sinh. Video nguồn,
hash nguồn, camera, ROI và lịch sử revision vẫn được giữ. Sau đó bấm **Chuẩn bị
lại** để tái tạo từ đúng video nguồn; nếu file mất hoặc hash đổi, ứng dụng giữ
ROI ở chế độ chỉ đọc và không gắn nhầm file khác.

Sau restart, ROI đã lưu vẫn còn. Một lần chuẩn bị bị ngắt giữa chừng được đánh
dấu lỗi rõ ràng và cần retry; ứng dụng không dùng output dở dang.

## Ý nghĩa số ID của V1

**Số ID theo dõi cục bộ trong clip** là số ByteTrack ID phân biệt trong một
lần chạy clip. Đây không phải số người duy nhất và không phải danh tính.
