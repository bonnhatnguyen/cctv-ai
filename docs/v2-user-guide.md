# Hướng dẫn V2 — ROI và dán nhãn hành động

V2 chạy cục bộ trên máy. Giai đoạn 1 mở clip, xem đúng khung hình nguồn và
khoanh/lưu vùng rổ tiền cố định. Giai đoạn 2 cho người vận hành tự gán và kiểm
tra năm nhãn hành động. Model cục bộ có thể đề xuất các đoạn cần xem quanh ROI,
nhưng không tự lưu nhãn, xác nhận event hay tạo coverage.

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

## Gán nhãn hành động

Sau khi ROI đã lưu, chọn bước **Gán nhãn**:

1. Chọn một **Lượt tay** đã có hoặc tạo lượt mới với tay trái, tay phải hay
   chưa rõ. Tracking là tham chiếu tùy chọn, không bắt buộc để gán nhãn.
2. Chọn đúng frame rồi dùng `I` đặt bắt đầu, `C` đặt frame qua biên và `O` đặt
   kết thúc. `C` chỉ áp dụng cho `hand_in` và `hand_out`.
3. Chọn nhãn bằng phím `1`–`5`: `hand_in`, `hand_out`, `take_out`, `put_in`,
   `unclear`. `unclear` có thể gắn cho toàn ROI khi không xác định được lượt
   tay, nhưng phải chọn lớp có thể xảy ra và lý do chưa rõ.
4. Chọn vật và mức quan sát, rồi bấm **Lưu nhãn** hoặc `Ctrl+S`. `Escape` bỏ
   bản nháp. Các khoảng chồng nhau được giữ thành những event riêng, không tự
gộp.

### Dùng Model hỗ trợ để bớt xem thủ công

Trong bước **Gán nhãn**, bảng **Model hỗ trợ** nằm trước form nhãn:

1. Chọn DINO hoặc MediaPipe đang sẵn sàng, nhập frame đầu/cuối (đều tính cả
   hai đầu), rồi bấm **Chạy model**. DINO dùng CUDA theo cấu hình; MediaPipe
   chạy CPU. V1 tracking, benchmark CLI và model hỗ trợ dùng chung một lượt
   inference nên run có thể ở trạng thái **Đang chờ**.
2. Khi hoàn tất, bấm **Đoạn x–y** để tới vùng cần xem. Nhãn và mốc qua biên có
   badge **ước lượng**; `Chỉ cần xem` nghĩa là model chưa đủ bằng chứng để chọn
   `hand_in` hay `hand_out`.
3. Bấm **Dùng làm nháp**, chọn đúng lượt tay và sửa nhãn/I-C-O sau khi xem frame
   chính xác. Chỉ khi bấm **Lưu nhãn** thì event draft mới được tạo. Hoặc bấm
   **Bỏ qua** nếu proposal sai.
4. Gợi ý rỗng không có nghĩa video không có hành động. Vẫn xem phần ngoài các
   proposal và chỉ ghi background bằng coverage có xác nhận ở bước **Kiểm tra**.
5. Dùng **Trạng thái gợi ý** để xem lại mục đang chờ, đã dùng, đã bỏ qua hoặc
   đã lỗi thời. **Lịch sử model** giữ các lượt hoàn tất, thất bại và đã hủy sau
   khi tải lại trang; lỗi mới nhất chỉ hiện cảnh báo khi đó là kết quả gần nhất.
   Hàng đợi chỉ tải tám mục mỗi lần; bấm **Tải thêm gợi ý** để xem trang kế
   tiếp. Lịch sử thu gọn mặc định để không làm rối form nhãn.

Run và gợi ý được lưu trong database annotation, không sao chép video hay lưu
thêm clip ngắn. Request/result/progress/log nhỏ được giữ trong private
`annotations/assistance/runs/<run-id>` cùng checksum để audit; artifact từng run
được chặn kích thước và toàn vùng assistance có quota/mức đĩa dự phòng. Sau khi tải lại trang, run đang chờ/chạy,
lịch sử và hàng đợi review được phục hồi. Hủy run hoặc tắt app sẽ dừng cả cây
process model/FFmpeg do app sở hữu; thiếu model không làm mất chế độ dán nhãn
thủ công.

Chọn một dòng trên timeline sẽ dừng video, tới đúng frame bắt đầu và mở event
để sửa. Có thể xác nhận, xóa mềm và khôi phục. Nếu mất kết nối, thử lại bản
nháp không tạo event trùng. Nếu tab khác đã sửa dữ liệu, ứng dụng tải revision
mới nhưng giữ bản nháp. Khi ROI đã đổi, overlay được đồng bộ và các mốc `I/C/O`
cũ bị xóa để bắt buộc đánh dấu lại trên ROI mới.

## Kiểm tra event và coverage

Ở bước **Kiểm tra**, xác nhận từng event sau khi xem lại hình ảnh. Xác nhận một
event chỉ nói rằng event đó đúng; nó không có nghĩa phần còn lại của video là
“không có hành động”.

Muốn xác nhận đã tìm đủ sự kiện trong một đoạn, đặt frame bắt đầu/kết thúc ở
**Phạm vi đã kiểm tra**, chọn riêng từng lớp đã xem hết và đánh dấu câu xác
nhận trước khi ghi. Chỉ coverage đang hiệu lực mới có thể tạo background cho
đúng lớp đó ở giai đoạn xuất dataset. Đoạn chưa có coverage luôn là **chưa
biết**. Khi event hoặc ROI liên quan thay đổi, coverage bị ảnh hưởng được vô
hiệu hóa và vẫn còn trong lịch sử để audit.

## Pilot guideline trước khi gán hàng loạt

Pilot cần cố định trước 20 đoạn, tối thiểu hai ví dụ cho mỗi nhãn rõ, hai
`unclear` và hai đoạn đã review không có hành động. Gán lượt một, chờ ít nhất
24 giờ, rồi dùng lịch xáo trộn mù cho lượt hai. Không xem nhãn lượt một trong
lượt hai.

`scripts/verify-v2-m2-pilot.py` kiểm manifest và tính agreement, temporal IoU
một-một cùng sai lệch crossing frame. Nếu video không đủ một lớp, ghi
`PENDING_DATA`; không đổi một hành động khác thành lớp còn thiếu. Công cụ này
đo độ nhất quán của guideline/người gán, không phải độ chính xác model.

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

## Benchmark model bằng CLI

Benchmark CLI riêng tư vẫn dùng để đo MediaPipe và Grounding DINO độc lập với
UI. Công cụ tạo artifact bất biến để đánh giá; bảng **Model hỗ trợ** dùng cùng
adapter để tạo hàng đợi review trong database. Cả hai đều không tự ghi nhãn,
xác nhận hoặc coverage. Quy trình benchmark,
đường dẫn output và cách phục hồi lỗi nằm trong
`docs/v2-assisted-benchmark-runbook.md`.

Nếu dữ liệu chưa có reference `hand_in`/`hand_out` và coverage đầy đủ, report
phải hiện `PENDING_DATA`. Kết quả đó chỉ là technical smoke, không phải bằng
chứng model đã đạt chất lượng hoặc đã giảm công gán nhãn.
