# Audit đặc tả V2 — annotation quanh rổ tiền

Ngày: 2026-09-10. Loại: audit nội bộ tài liệu, không có reviewer độc lập.
Baseline review: commit `d2adb3f`. Tài liệu chính:
`docs/superpowers/specs/2026-09-10-v2-basket-action-annotation-design.md`,
revision 2. Chỉ sửa tài liệu; chưa triển khai hoặc chạy gate sản phẩm.

## Phạm vi và bằng chứng đọc

Đối chiếu các quyết định người dùng (native, ROI rổ cố định, năm nhãn ngắn,
local/private, không technical debt) với handoff và V1 code hiện có:

- `backend/app/v1/media.py`: frame metadata/validation/SAR có sẵn, nhưng V2
  còn cần dịch vụ chọn frame chính xác, cache và lifecycle riêng.
- `backend/app/v1/database.py`: create_all chỉ bootstrap V1, chưa có migration
  cho annotation; không thể coi migration requirement đã được giải quyết.
- `backend/app/v1/settings.py`, `jobs.py`: dữ liệu job và annotation ở các
  vùng khác nhau; đường dẫn tuyệt đối lưu trong job cần được kiểm scope.
- `frontend/src/trackingApi.ts`: types V1 đang viết tay; generator chỉ áp dụng
  contracts annotation mới, tránh refactor không liên quan.
- `scripts/start-v1.ps1`: identity theo project/instance và process ownership
  cần được giữ; shortcut V2 không được trỏ vào dịch vụ V1.

## Phát hiện và cách đóng ở mức đặc tả

P1 = có thể làm sai nhãn/mất dữ liệu hoặc nghiệm thu sai; P2 = thiếu hợp đồng
thực thi/khả năng sử dụng. “Đã sửa” trong bảng chỉ có nghĩa yêu cầu đã rõ hơn,
không có nghĩa tính năng đã được chứng minh hoạt động.

| ID | Mức | Phát hiện | Điều chỉnh ở revision 2 | Gate |
| --- | --- | --- | --- | --- |
| A01 | P1 | hand_in/out chỉ 1–2 frame, dễ đếm rung mép | Interval chuyển động + crossing_frame; rung/che không xác định dùng unclear | §3, pilot M2 |
| A02 | P1 | Hai tay unknown và không track không phân biệt được | Interaction UUID theo lượt một tay; hand theo bên giải phẫu; ID V1 tùy chọn | §3, §6, M2 multi-hand |
| A03 | P1 | Unclear ở một tay xóa positive tay khác | Uncertain labels + scope; giữ positive độc lập; không ignore toàn video | §8, M3 mask fixtures |
| A04 | P1 | Review không định nghĩa đủ để tạo negative | Coverage theo lớp trên toàn ROI, phải xác nhận liệt kê hết sự kiện; phân biệt draft/unknown/background | §7–8, M3 |
| A05 | P1 | Đổi vị trí event chỉ vô hiệu hóa vùng mới | Invalidate hợp interval/lớp cũ và mới, gồm create/delete/restore | §7, M2/M3 |
| A06 | P1 | Chống leak theo parent nhưng schema thiếu parent | Parent recording, offset nullable, tập ngày qua đêm, provenance; grouping theo hash/day/parent | §6, §8, M3 |
| A07 | P1 | Sửa ROI cùng lúc lưu event gây revision lệch | Clip optimistic revision, transaction update, reject stale writes | §4, §7, M2 |
| A08 | P1 | Retry sau mất response sinh trùng hoặc báo xung đột giả | Operation UUID + payload hash + cached result atomically; kiểm replay trước expected revision | §7, M2 |
| A09 | P1 | Export vừa đọc vừa sửa thành snapshot hỗn hợp | Đóng băng read-snapshot/revision trước copy; crash reconcile publish/READY | §8, M3 |
| A10 | P2 | Frame chính xác nhưng có thể decode lại cả clip mỗi bước | Indexed/positioned decoding, bounded queue/cache và gate latency | §5, M1 |
| A11 | P2 | Proxy mới chưa có owner/status/retry | Preparing/ready/failed riêng, worker/subprocess ownership và cleanup staging | §5, M1 |
| A12 | P2 | Không có mở lại tập clip và thao tác annotation nhanh | Clip list, trạng thái, restore frame, shortcut keys và ROI zoom | §2, §5, M1/M2 |
| A13 | P1 | Một ví dụ mỗi nhãn không kiểm guideline nhất quán | Pilot cố định 20 đoạn, hai lượt; đo agreement/IoU/crossing riêng | §9 M2 |
| A14 | P2 | Schema version không ghi thay đổi ý nghĩa nhãn | Guideline version riêng, không trộn version trong training | §6, §8 |
| A15 | P2 | Hai DB liên kết nhưng chưa định nghĩa failure | Tạo annotation sau job commit, retry an toàn, giữ source; missing source vẫn archive nhãn được | §7–8, M1/M3 |
| A16 | P2 | Gate split đòi footage nhiều ngày có thể chặn giao công cụ vô hạn | Software split gate bằng fixtures; real export unsplit, phân biệt readiness training sau V2 | §9 M3 |
| A17 | P2 | Lẫn frame inclusive và thời gian kết thúc | [s,e] tương ứng [s/fps,(e+1)/fps), không lưu hai nguồn thời gian | §5, M1/M3 |
| A18 | P2 | Copy ID V1 bị hòa vào roadmap mà thiếu nghĩa/gate cũ | Ghi nguyên tên metric, disclaimer và đối chiếu CUDA JSONL trong M1 | §1, M1 |

## Các quyết định tránh phình phạm vi

- Giữ năm nút nhãn; interaction/uncertainty metadata phục vụ đúng vấn đề tay
  chồng nhau, không thêm hệ nhận dạng danh tính hoặc giao dịch.
- Không làm AI gợi ý ở V2; chưa có bằng chứng tiết kiệm công gán nhãn.
- Không buộc training dùng crop người hoặc classifier một nhãn; export giữ
  annotation đa nhãn và masks để adapter sau chọn bài toán rõ ràng.
- Không lấy thiếu dữ liệu làm lý do tự sinh label, tự nhận biết ngày quay hoặc
  báo accuracy. Unsplit là kết quả hợp lệ khi chưa có nhóm ngày độc lập.
- Versioning/migration và audit revision có mục đích cụ thể; không hứa schema
  sẽ bất biến mãi hoặc không bao giờ phát sinh lỗi.

## Phần chưa được kiểm chứng

1. Khả năng nhìn rõ crossing của tay và lấy/đặt vật trên shop clips: pilot M2.
2. Độ trễ frame service: gate M1 trên máy local và clip có index kiểm tra.
3. Hiệu quả thao tác UI/độ nhất quán nhãn: pilot M2, không suy từ mock tests.
4. Persistence/export/privacy boundary: integration, crash và browser gates
   ở từng milestone; hiện chỉ mới có đặc tả.

## Kết luận

Kiểm tra tài liệu: đối chiếu tên trường xuyên các mục, 18 mã phát hiện không
thiếu số lượng, không còn TODO/TBD/FIXME; `git diff --check` không báo lỗi
whitespace. Không chạy backend/frontend suites vì lượt này chỉ sửa Markdown;
không đưa kết quả kiểm tra từ khóa thành bằng chứng logic phần mềm đã đúng.

18 phát hiện đã có quy tắc xử lý và gate tương ứng trong spec revision 2.
Các điểm cần dữ liệu thật vẫn được ghi pending ở milestone liên quan. Audit
này đủ làm đầu vào cho việc viết plan M1; không thay thế review implementation
và không xác nhận sản phẩm V2 đã đạt chất lượng.
