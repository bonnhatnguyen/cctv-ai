# V2 — hỗ trợ gán nhãn tay quanh ROI

Ngày: 2026-09-11. Trạng thái: người dùng đã duyệt và yêu cầu viết plan bổ sung.
Plan chặng A: `../plans/2026-09-11-v2-assisted-labeling-benchmark.md`.
Chưa chạy benchmark model, chưa thay đổi runtime hoặc dữ liệu người dùng.

## 1. Vị trí và phạm vi

Nối tiếp `2026-09-10-v2-basket-action-annotation-design.md`, không đổi tên
V1/V2 hoặc đánh số lại M1–M3. Đây là phần mở rộng hỗ trợ annotation của V2,
không phải M3 export và không phải model giám sát hành động đã nghiệm thu.
Người dùng duyệt hướng giữ workspace hiện có, máy tạo gợi ý và người duyệt.

Spec gốc loại auto-label khỏi M1–M3. Tài liệu này bổ sung phạm vi riêng:
cho phép thử nghiệm read-only khi pilot M2 còn PENDING_DATA, không miễn các
gate cũ, không cho phép gọi dữ liệu training-ready hoặc tự chuyển sang train.
Ledger hiện tại ghi M2 software đã qua regression, pilot hai lượt còn thiếu;
plan M2 cũng còn full real-browser workflow chưa hoàn tất.

Mục tiêu là giảm thời gian tìm/chọn khoảng `hand_in`, `hand_out` trên video
shop. Không tự gán `put_in`, `take_out`, cash, người thực hiện hoặc giao dịch.
Không tự sinh `unclear` khi detector không thấy tay. Không huấn luyện,
thay person tracker V1, sửa toàn bộ UI, hoặc chuyển dữ liệu sang CVAT.

## 2. Phương án và quyết định

- MediaPipe Hand Landmarker: baseline pretrained đầu tiên, dùng video mode.
- Grounding DINO Tiny: đối chứng phát hiện tay bằng prompt cố định `hand.`.
  Dùng cùng các đoạn đánh giá; không giả định zero-shot có nghĩa chính xác
  trên tay nhỏ/bị che của CCTV.
- SAM 2.1 Tiny / Grounded-SAM-2: phương án bổ sung sau, chỉ xét nếu bằng
  chứng cho thấy tracking là điểm nghẽn. Không đưa cả stack vào ngay.

Chọn thử nghiệm so sánh trước, chưa chọn model thắng hoặc cam kết độ chính xác.
Không cài các dependency thử nghiệm vào môi trường V1/V2 đang dùng.
Môi trường benchmark riêng phải ghi phiên bản Python, package, thiết bị,
model revision và hash weights; không dùng revision `main` cho lần chạy đo.
Tải weights từ nguồn chính thức là bước setup rõ ràng, không tải ngầm khi API
khởi động. Inference local, không upload footage/nhãn, không remote inference.

## 3. Chặng A — benchmark độc lập, không ghi nhãn

Đầu vào là manifest private: clip UUID, source hash, ROI revision, guideline
version, fps phân số và khoảng frame inclusive. Resolve video qua clip/source
binding hiện có; không dùng tên file để đoán clip hoặc ROI.

Chọn và cố định các đoạn trước khi xem dự đoán: có vào/ra rõ, tay đứng yên,
đi ngang gần rổ, rung sát biên, nhiều tay, che khuất và đoạn không hành động.
Tận dụng footage hiện có, không ép một đoạn thành lớp còn thiếu. Nếu thiếu
ground truth đã review hoặc thiếu trường hợp cần đo, ghi PENDING_DATA.
Thử chạy suy luận kỹ thuật không cần chờ đủ nhãn; kết luận chất lượng thì cần.

Chia đoạn tuning và evaluation theo nguồn/recording, không rải frame liền kề
giữa hai tập. Nếu chỉ có một recording, báo kết quả exploratory trên camera
đó, không gọi là held-out generalization. Reference thủ công được chốt trước
khi xem output của model để hạn chế việc sửa nhãn theo dự đoán.

Decode tuần tự ảnh nguồn sạch theo index chuẩn V2. Xử lý ROI bounding rectangle
cộng vùng đệm, kẹp trong ảnh; lưu transform crop/resize/SAR để map kết quả về
raster nguồn chuẩn hóa. Không crop sát polygon đến mức mất phía ngoài rổ.
Frame sampling, margin và thresholds nằm trong một run config bất biến.
Cả hai detector nhận cùng lịch frame và cùng crop; so tốc độ phải ghi thiết bị.
Không đánh đồng phép đo end-to-end trên CPU/GPU khác nhau với tốc độ model thuần.

Detector adapter trả observation theo source frame, box/landmarks nếu có,
score kèm loại score, không giả làm event probability. MediaPipe handedness
score không phải độ tin cậy phát hiện tay. Không lấy box người V1 làm tay.
Timestamp video mode dẫn xuất từ index/fps nguồn; nếu cần lượng tử hóa tăng
dần cho API thì ghi mapping, tuyệt đối không dùng wall-clock để gán frame.

Association dùng chung cho phép so detector, giữ ID cục bộ từng run/clip và
reset ở khoảng gián đoạn. Không suy trái/phải giải phẫu hoặc person identity
từ ID. Không nối hai tay qua mất dấu/association mơ hồ để tạo crossing giả.

Tạo candidate `hand_in/out` khi có bằng chứng quan sát hai phía ROI của cùng
tay. Giữ quy ước biên nằm trong của guideline hiện tại; debounce/gap thresholds
chỉ điều khiển proposal, không thay định nghĩa nhãn. Tâm box/landmark chỉ là
ước lượng tâm bàn tay nhìn thấy. Nếu sampling bỏ frame, crossing chỉ là ước
lượng kèm bracket hai quan sát; không gọi là crossing exact đã xác nhận.
Khoảng xem có context trước/sau, phân biệt với start/end hành động đề xuất.
Chạm biên, mất dấu hoặc đầu/cuối clip thiếu bằng chứng chỉ tạo đoạn cần xem
không có action label; không gán ép hướng.

Báo cáo private phải có:

- Số đoạn, thời lượng, số event reference từng lớp và các lớp/trường hợp thiếu.
- Recall/precision event theo lớp, matching một-một cùng nhãn, temporal IoU
  inclusive >=0.5, tối đa tổng IoU và tie-break xác định; giữ cả FP/FN.
- Recall tìm đoạn: reference crossing nằm trong ít nhất một khoảng xem gợi ý;
  báo riêng, không đánh tráo với chất lượng nhãn/khoảng hành động.
- Sai lệch crossing/start/end trên cặp matched, không che event unmatched.
- Số proposal sai trên phút video đã review đầy đủ, không lấy unreviewed làm âm.
- Thời gian suy luận, peak RAM/VRAM khi đo được, thiết bị, cold/warm setup,
  lỗi và frame bị bỏ. Không có số đo thì ghi unavailable, không ghi zero.
- Thời gian người xem + sửa + xác nhận, kể cả rà đoạn ngoài proposal; so với
  thủ công trên các đoạn đối chứng và đảo thứ tự để giảm hiệu ứng nhớ video.

Không có ngưỡng phần trăm tự đặt rồi gọi đã được người dùng nghiệm thu.
Kết thúc A phải trình số đo, lỗi đại diện và lựa chọn tiếp: tích hợp model nào,
thử SAM2, hay chưa tích hợp. Người dùng duyệt lựa chọn và mục tiêu chất lượng
trước chặng B. Chưa có phép đo công sức thì chưa kết luận tiết kiệm thời gian.

## 4. Chặng B — ranh giới tích hợp sau khi chọn model

Đây là hợp đồng thiết kế, không cho phép bỏ gate A để xây UI ngay.
Plan B phải cụ thể hóa schema/routes và worker lifecycle trước implementation.

Giữ một workspace/player/exact-frame state. Nút “Tạo nhãn gợi ý” nằm ở bước
Gán nhãn; queue hiển thị đoạn xem với lựa chọn sửa, lưu nháp, xác nhận hoặc bỏ.
Chốt start/crossing/end dùng ảnh exact-frame hiện có, không dùng currentTime
hoặc frame lấy mẫu của detector làm bằng chứng đã xem.

Suggestion lưu tách bảng annotation với run ID, source hash, ROI revision,
guideline version, model/weights/config versions, frame interval, crossing
estimate/bracket, evidence và trạng thái review. Nhãn action nullable cho
đoạn chỉ cần xem. Không đưa raw suggestion vào export supervision.

Chấp nhận proposal tạo annotation qua cùng validation/revision/audit transaction
đang có, gắn provenance suggestion ID và model run; không ghi đè event cũ.
Người dùng chọn/tạo interaction, mặc định hand/object unknown. Không tự biến
track ID của model thành interaction ID hoặc anatomical hand. Retry idempotent
không tạo annotation trùng. Conflict giữ bản nháp, không silent overwrite.
“Lưu nháp” vẫn draft; “Xác nhận” là thao tác review rõ ràng.

ROI/guideline/source thay đổi làm suggestion stale, chặn accept và yêu cầu
chạy lại; hoàn tất job phải kiểm binding lần nữa. Sửa annotation không cần
chạy lại model nhưng accept luôn kiểm latest clip revision và xung đột.
Chạy lại giữ kết quả cũ có provenance, không sửa lịch sử nhãn đã xác nhận.
Reject proposal không tạo background; accept event không tạo coverage.
Người dùng vẫn phải rà ngoài proposal: không phát hiện không có nghĩa không
hành động. Đó là phần bắt buộc của đo recall và review dataset.

Worker inference có queue hữu hạn, cancel/deadline, một GPU job mỗi lúc;
không giữ transaction DB trong lúc suy luận và không chặn API lưu annotation.
Job queued/running/succeeded/failed/cancelled; crash khi running thành failed
có lý do interrupted, retry tạo run mới. Partial output không hiển thị là
hoàn tất. Model thiếu, OOM, decoder lỗi đều báo lỗi, không trả “0 hành động”.
Không tự fallback đổi model/thiết bị làm sai provenance.

Không lưu ảnh từng frame hoặc masks mặc định. Lưu metadata/proposal nhỏ,
debug artifacts opt-in dưới quota private có cleanup chỉ đụng file sở hữu.
Giữ source/ROI/labels/history. Source và media release/reprepare phải có
ownership/lease/cancel phối hợp, không đọc generation đã release hoặc tạo
bản copy video ngoài quota. Annotation-only workflow vẫn dùng được khi model
không cài hoặc bị tắt. Không thêm một DB nhãn hoặc video upload pipeline mới.

## 5. Tái sử dụng và bảo vệ baseline

Đã đối chiếu contracts, frame service, settings và plan M2 tại HEAD `474b6a9`.
`backend/app/vision/hands.py` thuộc legacy: dùng monotonic wall-clock, mapping
landmarks/handedness cần kiểm theo API thực tế, thresholds hard-coded và
association gần nhất. Không import nguyên tracker đó vào V2. Đặc tả không
yêu cầu sửa runtime legacy; adapter mới chỉ phụ thuộc model API đã kiểm chứng.
`backend/pyproject.toml` khai báo MediaPipe không chứng minh runtime đã cài
đúng phiên bản, weights tồn tại hoặc chất lượng trên camera đã qua gate.

Chặng A không migration DB, không API/UI mới, không ghi vào nhãn thật.
Chặng B dùng migration có backup/rollback tests, Python contracts là nguồn
sinh TypeScript, config tập trung; giữ V1 source_job binding, pixel/frame/SAR
semantics, private roots và các thao tác review hiện có.

## 6. Verification và điều kiện bàn giao

A cần tests deterministic cho crop mapping/SAR, source-frame timestamps,
ROI boundary, nhiều tay/ID swap, mất dấu, sampling bracket, clip boundary,
matching FP/FN và precision với thiếu coverage. Model adapter cần smoke test
weights thật; fake detector unit tests không được tính là model benchmark.
Reference, report và videos thật giữ Git-ignored; tài liệu chỉ ghi số liệu
tổng hợp được phép và tình trạng gate. Không tạo nhãn giả để PASS.

B cần thêm tests migration/replay/stale ROI/concurrent accept, export loại
raw suggestion, no-coverage-side-effects, cancellation/restart/OOM, quota,
switch clip khi inference đang chạy và browser review end-to-end. Chạy toàn
bộ regression backend/frontend, contract drift/build và ghi kết quả mới.
Không dùng số test M2 lịch sử để tuyên bố chức năng mới đã qua.

## 7. Nguồn tham khảo đã tra cứu

- https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker
- https://huggingface.co/IDEA-Research/grounding-dino-tiny
- https://huggingface.co/facebook/sam2.1-hiera-tiny
- https://github.com/IDEA-Research/Grounded-SAM-2

Hai model card Hugging Face ghi Apache-2.0 tại thời điểm tra cứu. Setup phải
ghi nhận license của đúng weights revision và dependencies được chọn, không
coi license repo tổng là license của mọi thành phần.

## 8. Self-review

- Không thay từ điển nhãn hoặc vượt gate training/M3 export.
- Tách candidate localization, action estimate và nhãn người xác nhận.
- Không hứa exact crossing từ sampling, detector score là event confidence,
  hoặc absence là negative; giữ nhánh xử lý thiếu dữ liệu và lỗi model.
- A là phần đầu tiên để lập plan; B có gate lựa chọn model/tiêu chí riêng,
  chưa được coi là implementation plan hay benchmark đã hoàn tất.
