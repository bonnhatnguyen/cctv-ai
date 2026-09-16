# V2 — tích hợp model hỗ trợ review nhãn quanh ROI

Ngày: 2026-09-12. Trạng thái: người dùng đã duyệt đặc tả; cập nhật làm rõ theo
8 findings audit plan đã được người dùng yêu cầu sửa. Implementation chưa bắt đầu.
Plan: `../plans/2026-09-12-v2-assisted-review-integration.md`.

## 1. Mục tiêu và sự thật chất lượng

Đưa model hỗ trợ vào đúng workspace V2 để giảm thời gian tìm các đoạn có tay
quanh ROI. Người vận hành chọn một khoảng frame, chạy model local, duyệt các
đoạn gợi ý, sửa các mốc và quyết định lưu hoặc bỏ.

Đây là **assisted review thử nghiệm**, không phải auto-label và không phải
model hành động đã nghiệm thu. Benchmark trên clip shop hiện có cho thấy
MediaPipe chỉ phát hiện 1/151 frame; Grounding DINO phát hiện trên 96/151 frame
nhưng tạo 33 đoạn review-only và chưa tạo được crossing `hand_in`/`hand_out`.
Vì vậy UI không được quảng bá precision/recall chưa đo, không gọi proposal là
nhãn thật và không suy rằng ngoài proposal là không có hành động.

Mục tiêu bàn giao đầu tiên:

- model và dependency vẫn chạy trong môi trường benchmark tách biệt;
- UI cho chạy model trên khoảng frame do người dùng chọn;
- proposal xuất hiện thành queue review và nhảy tới đúng frame;
- người dùng có thể dùng proposal để tạo nháp, sửa rồi lưu, hoặc từ chối;
- model không tự xác nhận annotation và không tạo review coverage;
- workflow dán nhãn thủ công vẫn hoạt động khi model thiếu hoặc lỗi.

Không nằm trong phạm vi: training/fine-tuning, M3 dataset export, nhận diện
tiền/người/giao dịch, chạy camera realtime, tự chọn threshold theo dữ liệu
evaluation, hoặc đưa model dependency vào `.venv` của API.

## 2. Quyết định kiến trúc

### 2.1 Model và ranh giới process

Grounding DINO Tiny là model mặc định thử nghiệm vì lần chạy thật tìm được
nhiều vùng tay hơn MediaPipe. MediaPipe vẫn là model tùy chọn nếu asset đã cài,
để tiếp tục thu bằng chứng trên camera khác. Danh sách model là registry đóng
với hai ID `dino` và `mediapipe`; API không nhận module/class/path tùy ý.

API không import Torch, Transformers hoặc MediaPipe. Một `AssistanceWorker`
duy nhất lấy job từ DB, ghi request JSON bất biến dưới private annotation root,
rồi khởi chạy worker subprocess bằng Python của `.venv-assist-benchmark`.
Subprocess dùng detector adapter, media sampling và proposal builder hiện có,
ghi result vào staging; parent kiểm schema, hash nguồn, ROI revision và guideline
lần nữa trước khi publish DB. Clip revision thay đổi do lưu annotation không
làm stale inference; expected clip revision vẫn kiểm lúc user mutation.
Thiếu interpreter/weights, decoder lỗi,
OOM, timeout hoặc child crash đều thành trạng thái lỗi có mã rõ ràng; không
được trả một danh sách rỗng như kết quả thành công.

Chỉ một assistance job chạy cùng lúc. V1, assistance và benchmark CLI cùng giữ
lease process-safe trong toàn vòng đời inference; job đợi lease ở trạng thái
queued. Lease dùng chung trên máy cho mọi source root/worktree; kiểm busy một
lần không thay được lease. Shutdown yêu cầu cancel process tree thuộc sở hữu; startup đổi job
`running` còn sót thành `failed/interrupted`. Không tự tải model khi API start
hoặc khi người dùng bấm chạy.

Interpreter/model root được phép chưa cấu hình; availability báo nguyên nhân
và manual mode vẫn boot. Device và sampling config được chốt/hash lúc enqueue.

### 2.2 Lưu trữ và audit

Thêm hai bảng vào annotation DB hiện có, qua migration có backup/rollback test:

- `assistance_runs`: run ID, clip/source/ROI/guideline binding, model asset và
  config hash, khoảng frame inclusive, trạng thái, progress, lỗi và timestamps;
- `assistance_suggestions`: proposal ID, run ID, label nullable, action/view
  spans, crossing estimate/bracket, reason/evidence nhỏ, review state và liên
  kết annotation nếu được chấp nhận.

Trạng thái run: `queued`, `running`, `succeeded`, `failed`, `cancelled`.
Trạng thái suggestion: `pending`, `accepted`, `rejected`, `stale`. Proposal chỉ
được hiển thị sau khi toàn run publish thành công; không hiển thị partial output.
Raw observation JSONL và metrics chi tiết ở artifact private có quota, không
lưu PNG/mask hoặc bản sao video. Không tạo database nhãn thứ hai.

Đổi source hash, ROI revision hoặc guideline làm pending suggestion cũ thành
`stale`; vẫn giữ lịch sử. Release prepared preview không xóa run/proposal vì
chúng gắn source gốc, nhưng chạy mới cần source hợp lệ. Cleanup chỉ được xóa
artifact do assistance worker sở hữu, không đụng source, ROI hoặc annotation.

### 2.3 API contract

Các route mới dưới `/api/v2/annotations`:

- `GET /assist-models`: model đã cấu hình, availability, device và lỗi setup;
- `POST /clips/{clip_id}/assist-runs`: tạo job idempotent với model,
  `start_frame`, `end_frame`, `operation_id`, `expected_clip_revision`;
- `GET /clips/{clip_id}/assist-runs/{run_id}`: trạng thái/progress/error;
- `GET /clips/{clip_id}/assist-runs`: phân trang history hoặc `active_only=true`
  để khôi phục queued/running jobs sau reload mà không cần biết run ID trước;
- `POST /clips/{clip_id}/assist-runs/{run_id}/cancel`: cancel job của clip;
- `GET /clips/{clip_id}/assist-suggestions`: queue, mặc định lấy pending của
  các run hiện hành và trả cả provenance cần hiển thị;
- `POST /clips/{clip_id}/assist-suggestions/{suggestion_id}/reject`: từ chối
  idempotent, không tạo annotation hoặc coverage.

`ActionAnnotationCreate` có thêm `suggestion_id` nullable. Khi có giá trị,
repository kiểm suggestion thuộc clip, run đã succeeded, binding còn hiện hành
và state pending. INSERT annotation trước, sau đó claim suggestion thành accepted
và gắn annotation ID trong cùng transaction; claim lỗi rollback toàn bộ. Tách
base request chung để Update không kế thừa suggestion_id của Create.
Validation/revision/overlap hiện có
vẫn là nguồn sự thật; retry cùng operation ID không tạo nhãn trùng. Nếu người
dùng sửa proposal trước khi lưu, annotation lưu giá trị người dùng đã chọn,
không sửa dữ liệu proposal lịch sử.

Accept event không tạo coverage. Reject suggestion cũng không tạo background.
Export M3 sau này chỉ đọc annotation do người xác nhận, không đọc raw proposal.

### 2.4 UI và luồng review

Trong bước **Gán nhãn**, thêm panel **Model hỗ trợ** trước editor nhãn:

1. Chọn model khả dụng; mặc định DINO nếu đã cài.
2. Đặt đầu/cuối từ frame chính xác hiện tại và bấm **Tìm đoạn cần xem**.
3. UI poll một run cụ thể với backoff có giới hạn; đổi clip hủy poll phía client
   nhưng không tự hủy job server. Có nút hủy rõ ràng khi queued/running.
4. Khi thành công, queue hiển thị từng proposal theo thời gian, label đề xuất
   hoặc “chỉ cần xem”, reason và bracket/estimate đều ghi rõ là ước lượng.
5. Chọn proposal dừng player và nhảy tới `view_span.start_frame`. Người dùng
   có thể xem đoạn, chọn **Dùng làm nháp** hoặc **Bỏ qua**.
6. **Dùng làm nháp** điền suggestion ID, label nếu có và các mốc ước lượng vào
   editor với banner “mốc do model gợi ý — cần kiểm tra”. Người dùng có thể sửa
   label/start/crossing/end, interaction, vật và visibility trước khi lưu.
   Proposal không có label giữ null; mọi đường lưu đều yêu cầu người dùng chọn
   nhãn và đủ lượt tay/mốc frame theo validation hiện có.
7. Chỉ nút **Lưu nhãn** hiện có mới ghi annotation. Sau khi lưu thành công,
   proposal chuyển accepted; xác nhận annotation vẫn là bước riêng ở Kiểm tra.

Queue có bộ lọc pending/đã xử lý nhưng không chen vào timeline annotation như
thể đã được gán. Empty proposal hiển thị “model không tìm thấy gợi ý; vẫn phải
xem phần còn lại”, không phải “không có hành động”. Model unavailable chỉ vô
hiệu panel model; không khóa editor thủ công.

## 3. Đồng thời, lỗi và an toàn dữ liệu

- Mọi mutation dùng operation ID và expected revision; không silent overwrite.
- Cancel dùng operation ID và run ID/state hiện hành, không phụ thuộc clip
  revision để vẫn hủy được job khi ROI vừa đổi. Accept/reject kiểm clip revision.
- Một run giữ binding snapshot, nhưng trước publish và trước accept đều kiểm
  source/ROI/guideline hiện hành. Stale trả 409 có code riêng và giữ UI draft.
- User đổi clip trong khi job chạy không làm proposal xuất hiện ở clip mới.
- Retry sau timeout/lost response dùng cùng operation ID; retry một run failed
  tạo operation mới và run mới, không sửa provenance run cũ.
- Deadline, frame limit, output quota và disk reserve lấy từ settings tập trung.
- Không chặn transaction DB trong inference. Publish result là transaction ngắn.
- API chỉ nhận frame range trong clip và model ID registry; không nhận URL,
  command, arbitrary filesystem path hoặc output directory từ client.
- Model asset hash/revision và inference config được lưu cùng run. Không silent
  fallback CPU/model/weights khác.

Migration v3 phải giữ reader benchmark tương thích v2/v3 và từ chối unknown
future schema. Thử app trên cùng data root cần dừng đúng owner launcher trước,
backup SQLite mới có binding/hash, rồi start feature với explicit data/model
paths. Ports khác không giải quyết lock của cùng root. Nếu v3 đã có user writes,
không tự restore v2 làm mất dữ liệu; runbook ưu tiên sửa trên v3.

## 4. Testing và nghiệm thu

Backend tests bắt buộc:

- migration/reopen/rollback và schema contract generation;
- create/list/cancel run, operation idempotency, invalid range/model;
- worker success/error/OOM/timeout/interrupted, bounded concurrency và cleanup;
- source/ROI/guideline stale trước publish và trước accept;
- accept atomic với annotation, retry không trùng, reject không tạo coverage;
- model dependency thiếu không ảnh hưởng manual annotation API;
- artifact path/quota/private-root validation và không mutate source.

Frontend tests bắt buộc:

- availability, chọn frame range và start job;
- polling success/failure/cancel, đổi clip không nhận response cũ;
- queue pending/reviewed, seek proposal, draft provenance và banner estimate;
- accept qua save hiện có, reject, stale/conflict giữ draft;
- empty result và unavailable không tuyên bố negative;
- manual workflow regression.

E2E thật trên Windows:

- cài sẵn asset, mở clip có ROI, chạy DINO trên khoảng ngắn;
- thấy progress và proposal sau khi run hoàn tất;
- mở một proposal, sửa exact frames, lưu draft rồi xác nhận riêng;
- kiểm DB/provenance và restart app vẫn thấy lịch sử;
- chạy model lỗi/thiếu asset vẫn tiếp tục gán nhãn thủ công.

Toàn bộ backend/frontend regression, generated-contract drift, TypeScript và
production build phải pass. QUALITY và EFFORT vẫn ghi `PENDING_DATA` cho đến
khi đủ reference/coverage và đo pilot; UI integration không tự nâng gate đó.

## 5. Phương án đã loại

- **Gọi benchmark CLI trực tiếp từ React:** nhanh nhưng browser phải biết path,
  không có lifecycle/audit/stale binding; tạo tech debt nên loại.
- **Import model vào FastAPI process:** làm API nặng, dễ OOM/crash cả annotation
  service và trộn dependency; loại.
- **Tự ghi annotation từ proposal:** làm ô nhiễm ground truth và khiến absence
  bị hiểu sai; loại.
- **Chờ model đạt accuracy rồi mới có UI:** không thu được effort thực tế và
  tiếp tục bắt người dùng dò full thủ công; thay bằng panel thử nghiệm có nhãn
  cảnh báo và gate rõ ràng.

## 6. Self-review

- Không đổi định nghĩa năm nhãn V2 hoặc M1–M3.
- Proposal, annotation và coverage có trạng thái/tác dụng tách biệt.
- Không hứa DINO/MediaPipe nhận diện hành động chính xác.
- Không thêm model dependency vào runtime API hoặc pipeline upload khác.
- Có lifecycle, idempotency, stale binding, provenance, quota và recovery thay
  vì nối tắt CLI vào UI.
- Phạm vi đủ cho một plan: persistence/API/worker là backend feature; panel
  review là frontend consumer của cùng contract, không phải subsystem độc lập.
