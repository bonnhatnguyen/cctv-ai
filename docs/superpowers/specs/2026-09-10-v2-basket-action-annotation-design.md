# V2 — công cụ gán nhãn hành động quanh rổ tiền

Ngày: 2026-09-10. Revision tài liệu: 2, sau audit nội bộ. Trạng thái: đặc tả
đã chỉnh để lập plan; các gate thực nghiệm bên dưới chưa chạy. Người dùng đã
chọn công cụ native trong ứng dụng, một ROI rổ tiền
cố định và năm nhãn `hand_in`, `hand_out`, `take_out`, `put_in`, `unclear`.
Các lựa chọn kỹ thuật dưới đây cụ thể hóa hướng đã duyệt; chưa có code V2 mới.
Audit record: `docs/v2-spec-audit.md`. Ngưỡng nghiệm thu ở đây là mục tiêu để
thử công cụ, không phải kết quả đo hoặc cam kết độ chính xác model.

## 1. Vị trí trong dự án tổng

Mục tiêu dài hạn là học từ video shop để hỗ trợ xem lại chuỗi tương tác quanh
tiền và hàng hóa. V1 đã nghiệm thu tracking người trên MP4 offline. V2 bổ sung
dữ liệu hành động có người xác nhận; đây là đầu vào cho một giai đoạn huấn luyện
và đánh giá riêng sau V2.

Luồng phát triển: V1 person tracking → V2 ROI + nhãn thời gian + review + xuất
dataset → thử nghiệm model trên dữ liệu shop → đánh giá trên ngày quay giữ riêng
→ hỗ trợ tìm đoạn cần xem. Khả năng kết luận giao dịch chưa nằm trong V2.

Nguồn kế thừa: `docs/V2-HANDOFF.md`, `docs/v1-acceptance.md`, thiết kế/plan
offline V1 ngày 2026-09-09. Tag `v1-stable-2026-09-10` trỏ tới
`a7f42faffed1b6e969d0d025ae3809423508bec8`. Các con số test/GPU trong tài liệu
V1 là bằng chứng lịch sử, chưa phải kết quả chạy lại V2.

Thiết kế RTSP ngày 2026-09-08, `docs/pilot-runbook.md`,
`data/labels/README.md` và các module legacy transaction không là hợp đồng V2.
Không nhập các ngưỡng frame, quy tắc giao dịch hoặc nhãn review_required từ đó.

Bản nháp `2026-09-10-v2-truthful-local-tracking-design.md` và plan cùng tên chỉ
còn là tham khảo cho vấn đề copy local ID; roadmap V2 trong chúng được thay thế
bởi tài liệu này. Chưa thực thi plan cũ. M1 sửa nhãn hiển thị thành “Số ID theo
dõi cục bộ trong clip”, kèm “không phải số người duy nhất”, giữ phép tính backend
và đối chiếu JSONL của một job CUDA thật. Shortcut có gate bàn giao M3.

## 2. Kết quả người dùng nhận được

Trong ứng dụng CCTV AI cục bộ, người dùng mở clip, khoanh rổ, xem chậm hoặc bước
từng frame, chọn khoảng bắt đầu/kết thúc và bấm một trong năm nhãn. Có thể sửa,
xóa nhầm rồi khôi phục, mở lại phiên gán nhãn, review và xuất dataset riêng tư.
Mỗi nhãn cho biết đoạn nào trong video nguồn đã được xem và kết luận gì từ hình
ảnh. Danh sách clip có tên nguồn, trạng thái chuẩn bị/gán nhãn/review, mở lại
clip và khôi phục frame đang xem. V2 hoạt động được ngay khi chưa có model hành
động hoặc person tracking. Dữ liệu đã lưu ở backend; localStorage chỉ giữ lựa
chọn UI. Chỉ số review tính từ hợp các khoảng đã xác nhận, không cộng trùng.

V2 không cần public dataset để vận hành. Training, auto-label, pose, nhận diện
tiền, nhập dataset ngoài, RTSP, vai trò người bán/khách và cảnh báo là các hạng
mục riêng sau này. Ngôn ngữ gọn trên UI; cấu trúc dữ liệu chi tiết nằm phía sau.

## 3. Từ điển nhãn và quy tắc xem video

ROI khoanh phần rổ quan sát được, gồm miệng rổ; người dùng dùng cùng quy tắc
khoanh giữa các clip của cùng góc camera. ROI chỉ là vùng ảnh 2D. Nó không chứng
minh tay/vật ở bên trong rổ theo chiều sâu.

| Nhãn | Bằng chứng cần thấy | Khoảng frame được đánh dấu |
| --- | --- | --- |
| hand_in | Tay nhìn thấy chuyển từ ngoài vào vùng rổ | Từ bắt đầu chuyển động đưa tay vào đến khi dừng hoặc đổi hướng sau khi vào |
| hand_out | Tay nhìn thấy chuyển từ vùng rổ ra ngoài | Từ bắt đầu chuyển động rút tay ra đến khi dừng hoặc đổi hướng sau khi ra |
| take_out | Thấy vật ở rổ rồi được tay mang ra | Từ bắt đầu thao tác lấy vật nhìn thấy đến khi vật ra ngoài rõ ràng |
| put_in | Thấy vật được tay mang đến và đặt vào rổ | Từ bắt đầu đưa vật vào đến khi nhìn thấy hoàn tất đặt vật |
| unclear | Có tương tác quanh rổ nhưng bị che hoặc thiếu bằng chứng để phân loại | Khoảng tương tác không thể xác định |

Đầu đoạn hand_in/out là frame đầu chuyển động liên tục theo hướng tương ứng
sau lần đứng yên/đổi hướng gần nhất; cuối đoạn là frame đầu dừng/đổi hướng sau
chuyển tiếp. Lưu thêm `crossing_frame`: frame đầu tâm bàn tay nhìn thấy ở phía
đích, với frame trước đó nhìn thấy ở phía xuất phát. Crossing phải nằm trong
interval. Không cần vẽ điểm bàn tay hay chạy pose trong V2. Với take_out/put_in
và unclear, crossing_frame là null.

Quy ước vào/ra: tâm phần bàn tay nhìn thấy nằm trong/ngoài polygon; nằm đúng
đường biên được tính trong. Chỉ gán hand_in/out khi xác định được chuyển động
và biên; nếu tay rung ở mép hoặc bị che khiến không chốt được phía/chuyển tiếp,
gán unclear cho phần đó. Không auto đếm mỗi lần chạm mép hoặc dùng debounce
threshold. Nếu nhìn rõ hai chuyển động độc lập theo hai hướng thì giữ hai nhãn.
Tay đứng yên không tạo event mới. Nếu đầu/cuối thao tác bị cắt bởi clip, dùng
unclear với reason=clip_boundary, không tạo đoạn hành động đầy đủ giả.

`hand=left|right|unknown` theo bên giải phẫu của người thực hiện, không theo bên
trái/phải màn hình. Hai tay có thể tạo hai event riêng.

Take_out/put_in cần thấy thao tác với vật, không chỉ chuyển động tay qua vùng.
Chỉ chọn `object=cash` khi thấy đó là tiền; rổ tiền không đủ để tự điền cash.
Thao tác hand_in và put_in có thể chồng thời gian: đây là dataset đa nhãn.
Không tự sinh nhãn tay từ nhãn vật hoặc ngược lại.

`unclear` không phải mẫu âm và không phải lớp hành động để mặc định training.
Nhãn này giữ tên ngắn trên UI nhưng lưu `uncertain_labels` là tập con không
rỗng của bốn hành động, mặc định cả bốn; `reason=occlusion|boundary_ambiguous|
clip_boundary|actor_ambiguous|object_ambiguous`. Nếu chỉ không biết vật là tiền
hay loại khác nhưng thấy rõ thao tác lấy, gán take_out với object=unknown.

Mỗi nhóm thao tác tay liên tục quanh rổ có `interaction_id` UUID cục bộ trong
clip, hiển thị “Lượt 1”, “Lượt 2”. Người dùng tạo/chọn nhóm; hand_in, take_out,
hand_out của cùng tay có thể thuộc một nhóm. Hai tay/nhóm không tự merge theo
person ID. Nhóm chỉ liên kết thao tác quan sát được, không là người hoặc giao
dịch. Khi không gán được cho nhóm, unclear dùng `scope=roi` và interaction_id
null; nhãn rõ luôn có `scope=interaction` và một nhóm. Nhóm có hand cố định;
đổi hand invalidates review của cả nhóm.

Unclear của cùng nhóm và cùng lớp không chồng interval hành động confirmed:
API trả 409 kèm event xung đột, yêu cầu chia/sửa đoạn. Unclear khác nhóm hoặc
scope=roi được phép chồng; giữ positive của nhóm đã nhìn rõ trong export.
Mọi đoạn chưa review đều chưa phải mẫu âm.

## 4. ROI cố định và tọa độ

Mỗi camera setup có tên do người dùng đặt và một polygon `basket` đơn, không
tự cắt, tối thiểu ba đỉnh, diện tích dương. Tọa độ chuẩn hóa x/y trong [0,1]
theo raster video nguồn. UI ánh xạ qua vùng ảnh thực, bù letterbox và SAR;
không lưu tọa độ theo kích thước cửa sổ browser.

Template ROI có revision bất biến. Clip mới cùng setup nhận bản gợi ý template
và phải được xác nhận trên ảnh của clip đó, kể cả khi kích thước giống nhau.
Crop, góc quay hoặc vị trí rổ đổi thì chọn setup mới hoặc sửa ROI cho clip.
Không tự suy ra setup từ tên file.

Clip gắn vào snapshot ROI revision cụ thể. Thay template chỉ áp dụng clip mới.
Nếu sửa ROI của clip đã có nhãn, tạo revision mới, giữ revision trước để khôi
phục và đánh dấu nhãn cùng review coverage cần review lại. Cập nhật ROI dùng
clip revision trong cùng transaction để tránh nhãn được lưu vào ROI cũ cùng
lúc. Chỉ snapshot training của clip đã xác nhận lại được xuất; archive vẫn
giữ đầy đủ mọi revision cùng trạng thái chưa review.

## 5. Thời gian và frame chính xác

Nguồn sự thật là `start_frame`/`end_frame` zero-based, inclusive, thỏa
0 <= start <= end < decoded_frame_count. Thời gian hiển thị được dẫn xuất từ
frame và fps_num/fps_den của nguồn CFR; không lưu một cặp timestamp độc lập có
thể trôi khỏi frame. Một interval inclusive [s,e] có support thời gian
[s * fps_den/fps_num, (e+1) * fps_den/fps_num); giữ giá trị phân số khi xuất,
chỉ làm tròn để hiển thị. Tôn trọng định dạng V1: MP4 CFR và kích thước chẵn.

Player phục vụ xem liên tục, tốc độ 0.25/0.5/1 và loop đoạn chọn. Việc chốt
frame dùng ảnh tĩnh do backend giải mã tại đúng index nguồn; bước trước/sau
di chuyển index ±1. Chỉ cho chốt khi ảnh tương ứng đã tải, hủy/loại response
cũ khi người dùng tua nhanh. Không coi currentTime của HTML video là bằng
chứng đã hiển thị đúng frame.

Backend chỉ nhận UUID clip và index, không nhận đường dẫn tùy ý. Giải mã ảnh
bằng cùng quy tắc thứ tự frame dùng cho index; cache private có giới hạn và
gắn hash nguồn. Không dùng approximate keyframe seek để chốt annotation.
Nguồn không browser-playable vẫn có bước-frame; preview liên tục dùng proxy
local silent H.264 được kiểm tra cùng số frame/fps/SAR trước khi cho dùng.
Preview lỗi hiển thị rõ; không đổi nguồn hay lén thay mốc thời gian.

Clip có trạng thái chuẩn bị riêng `preparing|ready|failed|releasing|released` cho frame index và
preview, không ghi đè JobStatus tracking V1. Backend dựng index/kiểm frame count
trước ready; annotation UI chỉ mở khi ready. Preview có thể tái tạo từ nguồn,
không dùng video đã vẽ box làm nguồn training. Lỗi chuẩn bị có retry rõ ràng.

Frame service phải dùng decoder tuần tự giữ vị trí hoặc index seek đã kiểm
chứng rồi decode đến index đích, không decode lại từ đầu cho mỗi phím bước.
Một worker chuẩn bị/giải mã riêng có hàng đợi tối đa 32 request; tác vụ không chặn API
lưu nhãn. Decode/encode là subprocess do V2 sở hữu, startup dọn staging chưa
hoàn tất của chính dịch vụ và đánh dấu retry; không tiếp tục báo ready giả.
Cache ảnh có hard budget 512 MiB; cache và queue limit cấu hình một nơi,
không tính source, proxy và frame index vào cache ảnh. Eviction không đụng source/nhãn;
đầy cache thì giải mã trực tiếp hoặc báo lỗi, không trả nhầm frame.

Phím mặc định: Space phát/dừng, Left/Right bước frame khi dừng, I/O chốt
đầu/cuối, 1–5 chọn nhãn theo thứ tự bảng, C chốt crossing, Ctrl+S lưu,
Escape hủy lựa chọn. Không bắt phím khi nhập text; có nút tương đương.
Hiển thị ảnh toàn cảnh và zoom quanh ROI, giữ một phép ánh xạ tọa độ.
Chọn nhóm gần nhất là gợi ý UI, người dùng thấy và có thể đổi trước khi lưu.

## 6. Bản ghi và trạng thái dữ liệu

Hợp đồng backend typed là nguồn sự thật. Sinh TypeScript types từ schema được
kiểm tra trong build, không viết hai bộ enum action độc lập. Áp dụng generator
cho contracts annotation mới; không mở rộng thành refactor toàn bộ API V1.

| Bản ghi | Nội dung chính |
| --- | --- |
| Clip | UUID, source hash, source job UUID, frame count, fps phân số, kích thước/SAR, recording_days, camera setup, parent_recording_id, parent_offset_ms, provenance_state |
| ROI revision | UUID, setup, revision, polygon, clip binding, thời điểm tạo |
| Interaction | UUID, clip UUID, hand, optional track reference, revision; một lượt của một tay, không là danh tính |
| Annotation | UUID, clip UUID, ROI revision, interaction_id hoặc ROI scope, label, start/end/crossing frame, object, visibility, uncertain_labels/reason nếu unclear, review state, guideline_version |
| Revision | revision number, previous revision, payload, operator local, UTC save time, thao tác create/edit/delete/restore/review |
| Review coverage | Khoảng frame, scope toàn ROI/mọi tay, reviewed_labels, ROI revision, clip revision, guideline_version; xác nhận đã liệt kê hết sự kiện của các lớp được chọn |
| Dataset export | UUID, schema version, revision manifest, split mapping, hashes, trạng thái xuất |

`object=cash|other|unknown`; hand_in/out và unclear mặc định unknown, không ép
người dùng đoán. `visibility=clear|occluded`; thiếu hoàn toàn bằng chứng của
tương tác được biểu diễn bằng unclear, không thêm nhãn suy đoán.
`review_state=draft|confirmed|needs_review`; deleted là revision tombstone,
không xóa lịch sử. Confirmed nghĩa người dùng đã review: một unclear confirmed
vẫn là nhãn không xác định, không thành hành động dương/âm.

Track reference là `(tracking_job_id, local_track_id)` tùy chọn, được xác minh
thuộc cùng nguồn và có evidence thực cho ID đó. Reference đặt trên interaction.
V1 ID không phải khóa danh tính; ID đổi không là lý do cắt event, người dùng có
thể bỏ reference và giữ nhóm thao tác đang thấy. Không bắt buộc tracking lại
để gán nhãn; không merge/re-identify người trong V2.

`guideline_version` định danh đúng quy tắc gán nhãn, độc lập schema version.
Sửa ngữ nghĩa nhãn tạo guideline version mới; không trộn chúng trong training
snapshot. UI hiển thị hướng dẫn hiện hành. Mỗi version có định nghĩa, ví dụ
dương, ví dụ dễ nhầm và quy tắc thiếu quan sát được người dùng review.

`recording_days` là tập ngày quay (YYYY-MM-DD, múi giờ Asia/Saigon); clip qua
nửa đêm mang cả hai ngày, chưa biết thì tập rỗng. `parent_recording_id` nullable
khi provenance unknown, là nhóm nguồn lưu trữ do người
dùng xác nhận; mọi đoạn cùng recording gắn cùng nhóm, không sinh nhóm riêng
mỗi lần import excerpt. `parent_offset_ms` là offset bắt đầu trong recording,
nullable nếu không biết; chỉ dùng truy vết, không là mốc nhãn hay phép quy đổi
frame. `provenance_state=unknown|user_confirmed`: không gọi xác nhận thủ công
là kiểm chứng tự động. Lưu được nhãn khi provenance chưa rõ, nhưng chỉ xuất
unsplit. Không yêu cầu người dùng đoán ngày/offset để đi tiếp.

Schema version là phiên bản hợp đồng export; DB có migration version riêng.
Versioning cho phép thay đổi có kiểm soát, không có lời hứa rằng sẽ không bao
giờ cần migration. Không auto đoán/đọc schema không biết.

## 7. Kiến trúc triển khai và lưu trữ

Tái sử dụng luồng import và media validation của V1 trong repository V2.
Thêm router `/api/v2/annotations` vào app factory hiện có, sử dụng chung job
service; giữ một bản implementation import/tracking. Tách module annotation
theo trách nhiệm: contracts, repository/migrations, frame service, export,
API. Frontend gồm workspace, ROI editor, frame controls, timeline/event editor
và API client typed. Không nối runtime RTSP/transactions legacy.

Backend V1 hiện chỉ có create_all, không coi đó là migration cho annotation.
Annotation DB có migration version riêng từ M1; mọi connection bật foreign key.
Clip/source job nằm ở hai DB: tạo liên kết sau khi job V1 commit, lookup lại
nguồn trước khi chuẩn bị hoặc export. Lỗi tạo annotation clip giữ imported job
cho retry, không xóa source và không cần distributed transaction. V2 không
cung cấp xóa video; tombstone chỉ áp dụng nhãn. Source bị xóa ngoài ứng dụng
thì clip unavailable, nhãn/lịch sử vẫn đọc và archive annotations-only được.

SQLite annotation là nơi lưu chính thức. Bổ sung sau audit plan M1: root mặc
định là `resolved_v1_data_dir/annotations` (thông thường `data/v1/annotations/`),
override tuyệt đối được kiểm binding về source data root. Mỗi DataDirectory
có annotation DB/lock riêng; không dùng sibling chung theo parent. Root phải
được Git-ignore trước khi có dữ liệu. JSON export là snapshot bất biến, không
là DB thứ hai cần đồng bộ. Các cache frame và staging export ở cùng private
root, không vào Git. Nhãn thật cũng là dữ liệu riêng tư, không chỉ video.

Bổ sung vòng đời derived media sau audit plan M1: người dùng có thể giải phóng
chunks/preview/cache được service tạo cho clip, giữ video nguồn, ROI, nhãn và
lịch sử. State releasing chặn reader mới và đợi/đóng owned readers có giới hạn
trước dọn; released cho phép chuẩn bị lại bằng source hash cũ. Release không
đổi ngữ nghĩa ROI/nhãn và không hủy review; source đổi thì reprepare fail.
Quota phải có thao tác thu hồi rõ ràng; không tự xóa source hoặc nhãn để giải
phóng chỗ. Quy tắc này bổ sung quản lý tài nguyên M1, không mở tính năng xóa
video đã bị loại khỏi V2.

Lưu event cùng revision trong một transaction; UI có nút Lưu và trạng thái
đang lưu/đã lưu/lỗi. Không báo đã lưu trước phản hồi server. Mọi mutation của
clip gửi expected_clip_revision, tăng revision đơn điệu trong transaction;
HTTP 409 giữ bản nháp cho người dùng so sánh/tải lại, không overwrite thầm lặng.
UI chỉ áp dụng response có clip revision không cũ hơn revision đang hiển thị;
retry trả kết quả cũ không được kéo UI về bản nhãn cũ.
Operation UUID lưu cùng payload hash và kết quả mutation trong transaction:
retry cùng payload trả đúng kết quả cũ trước khi kiểm expected revision; dùng
lại UUID với payload khác trả 409. Không sinh event trùng khi mất response.

Review coverage là xác nhận đã liệt kê đầy đủ sự kiện/unclear của từng lớp
trong khoảng trên toàn ROI, bao gồm mọi tay; không tự tạo từ lịch sử playback.
Thêm/sửa/xóa/restore event hủy coverage của hợp interval cũ và mới cho các lớp
cũ và mới liên quan; sửa uncertain_labels cũng áp dụng cùng quy tắc. Các phần
coverage ngoài khoảng ảnh hưởng được giữ. Nhãn đã sửa về draft; các nhãn khác
không thay đổi vẫn confirmed. Sửa nhóm/ROI invalidates phạm vi nhóm/toàn clip.
Confirm event riêng không tự xác nhận phần còn lại của đoạn là background.

Lỗi disk/DB hiển thị và giữ bản nháp đang mở để retry; chuyển clip/rời trang có
cảnh báo chưa lưu. Bản nháp chưa server-ack có thể mất nếu process/browser chết;
không gọi đó là đã lưu. Sau restart đọc lại đúng revision đã commit.

Chỉ dùng nguồn job có resolved path bên trong private root của repository V2.
DB copy từ V1 có thể chứa absolute paths cũ: không sửa/rebase hàng loạt và
không mở đường dẫn ngoài V2. Dùng luồng import hiện có để tạo job V2 mới từ
clip được chọn. Thiếu file/hash không khớp trả lỗi rõ, không đổi nguồn sau nhãn.

## 8. Xuất dataset để training sau V2

Export snapshot gồm manifest, clip metadata, ROI, annotations và review
coverage. Mỗi bản ghi có schema version, nguồn shop_manual, operator/review
provenance, guideline_version và hash nguồn. Chọn mục đích `archive` (lưu toàn
bộ lịch sử/trạng thái) hoặc `training` (snapshot nhãn và mask theo bảng dưới).
Hai kiểu đóng gói: annotations-only hoặc portable
gồm bản sao video gốc cùng relative paths. Bản portable vẫn hoàn toàn local;
copy và kiểm hash, không move nguồn. Không tự cắt/re-encode training samples.

Train/validation/test được chọn theo recording_days đã xác nhận, không suy
từ thời gian import. Tạo nhóm liên thông các clip chia sẻ bất kỳ ngày quay,
source hash hoặc parent_recording_id; mỗi nhóm ở đúng một split. Clip qua đêm
liên kết cả hai ngày. Kiểm cả các manifest split đã lưu: cùng group không được
gán khác split trong cùng dataset lineage. Lineage là UUID định danh một phân
hoạch train/validation/test, lưu trong manifest và split registry của annotation
DB; registry và export snapshot metadata được ghi cùng transaction. Nhóm mới
nối các assignment khác split phải bị từ chối. Đổi split tạo lineage mới có cảnh
báo mất tính độc lập với kết quả đánh giá cũ; không lặng lẽ sửa manifest.

Snapshot ba split yêu cầu ba nhóm độc lập không rỗng, user_confirmed provenance
và parent_recording_id. Thiếu dữ liệu thì xuất unsplit. Parent offset có thể
null vì toàn bộ parent đã cùng split. Không khẳng định đã phát hiện mọi đoạn
trùng nếu metadata người dùng nhập sai; hash chỉ xác định trùng byte.

Training export là annotation đa nhãn + mask theo từng lớp; không dựng sẵn
crop theo từng người vì V2 chưa có spatial actor ground truth. Mỗi mask giữ
scope interaction/roi và interval inclusive. Quy tắc cho bốn lớp hành động:

| Dữ liệu | Ảnh hưởng training |
| --- | --- |
| Positive confirmed theo guideline/ROI hiện hành | Giữ event và interaction_id, kể cả có unclear của nhóm khác cùng thời gian |
| Unclear confirmed | Ignore cho uncertain_labels tại nhóm chỉ định; scope=roi là không biết nhóm nào, cấm suy negative ở ROI cho các lớp đó |
| Draft/needs_review | Exclude supervision của event; interval chặn negative cho lớp liên quan, không xóa positive hợp lệ khác |
| Coverage xác nhận đầy đủ một lớp trên toàn ROI | Chỉ phần không có positive, unclear, draft/needs_review của lớp đó mới là background của lớp đó |
| Chưa review hoặc coverage invalid | Unknown/exclude, không phải background |

Toàn ROI có positive của lớp L khi có ít nhất một event L confirmed; khi không
có positive thì chỉ có negative nếu coverage L đầy đủ và không có uncertainty
ở bất kỳ nhóm nào. Điều này chỉ dùng cho bài toán “có hành động L quanh rổ”;
không suy số lần/ai thực hiện từ mask ROI. Negative toàn bộ bốn lớp là giao
background của bốn lớp. Export giữ positive và uncertainty riêng, không áp
một mask ignore toàn video. Review một lớp không review thay ba lớp còn lại.

Archive cho phép mọi trạng thái; training từ chối clip đang stale vì sửa ROI
hoặc có guideline cũ chưa review lại, trả danh sách clip cần xử lý. Không tự
bỏ clip khỏi selection. Có draft thông thường thì xuất event loại exclude như
bảng, manifest ghi số lượng positive/ignore/exclude/background theo lớp.

Chụp DB read-snapshot ngắn để đóng băng revision list trước khi copy; sửa nhãn
trong lúc export không làm nửa cũ nửa mới. Nguồn đã đăng ký được coi bất biến,
kiểm lại hash khi export. Manifest ghi rõ revision đã chụp, không gọi nó là
revision mới nhất nếu đang có sửa khác. Riêng archive annotations-only vẫn
cho phép nguồn thiếu/đổi hash: ghi source_status=missing|hash_mismatch và hash
đăng ký cũ, không tuyên bố đã verify media. Training hoặc portable gặp trường
hợp này phải fail trước publish.

Ghi ra staging UUID, xác minh tất cả file/hash và manifest rồi publish thư mục
snapshot hoàn chỉnh bằng rename cùng filesystem. DB chỉ đặt READY sau publish;
startup reconcile trường hợp crash giữa rename và cập nhật DB bằng manifest
hash, nếu không đủ bằng chứng giữ failed và cho retry UUID mới. Lỗi không
xuất link READY, không ghi đè snapshot cũ. API chỉ ghi private export root;
người dùng tải bundle qua loopback, không truyền đường dẫn đích tùy ý.
Export schema canonical đa nhãn; adapter training cụ thể làm ở milestone sau.

## 9. Chia milestone và cổng nghiệm thu

Mỗi milestone có spec/plan chi tiết, test tái hiện yêu cầu trước implementation,
review, acceptance record và commit local riêng. Chỉ bắt đầu milestone sau khi
gate trước qua. Ledger V2 là `docs/v2-acceptance.md`; không sửa lịch sử V1.

### M1 — mở clip và khoanh rổ

Một workflow hoạt động: import clip → mở workspace → xem/bước frame → vẽ ROI
→ lưu → reload. Bao gồm contracts, migration, frame service và UI cần thiết.
Test polygon tự cắt/ngoài ảnh, ánh xạ letterbox/SAR 2:1 và response frame cũ.
Fixture video có số frame in sẵn: bước đầu/giữa/cuối đọc đúng index. Clip shop
thật: ROI nằm đúng rổ tại đầu/giữa/cuối; sửa template không đổi snapshot clip
trước. Bảo toàn hash nguồn và V1 playback; label local ID được làm rõ tại UI.

M1 phải mở annotation từ job IMPORTED mà không start tracking, liệt kê/mở lại
hai clip sau reload, và xác nhận job V1 giữ trạng thái cũ trong lúc chuẩn bị.
Chạy fixture codec cần proxy, kiểm số frame/fps/SAR; test crash/retry chuẩn bị
không ghi READY sớm và test source reference ngoài V2 bị từ chối.

Gate hiệu năng trên máy local hiện có: một clip shop 30–60 giây và fixture CFR
10 phút có frame index in sẵn; sau preparing=ready, đo 50 lần bước frame gần
nhau và 20 lần seek rải đầu/giữa/cuối. P95 tính nearest-rank từ lúc nhấn đến
khi ảnh đúng index hiển thị: bước gần <=250 ms, seek xa <=2 s; 100% index đúng.
Ghi điều kiện cache và thời gian chuẩn bị riêng, không gộp để che độ trễ.
Nếu không đạt, tối ưu frame service trong M1, không hạ tiêu chí thầm lặng.
Gate này kiểm responsiveness công cụ; không là benchmark model/CUDA.

### M2 — năm nhãn, chỉnh sửa và review

Chọn khoảng, tạo/sửa/xóa/khôi phục đủ năm nhãn; hand/object/visibility và track
reference tùy chọn; revision conflict/retry được test bằng hai tab. Reload và
restart giữ revision đã lưu; chặn interval ngoài frame range; overlap hợp lệ
được giữ. Sửa ROI hoặc event làm mất review tương ứng đúng quy tắc.

Pilot guideline trước gán nhãn hàng loạt: chọn cố định 20 đoạn tương tác/ngữ
cảnh từ clip shop, có tối thiểu hai ví dụ mỗi hành động, hai unclear và hai
đoạn đã review không có hành động. Lưu danh sách đoạn/hash trước khi đo; không
chọn lại riêng mẫu dễ sau khi thấy kết quả. Thiếu mẫu thì gate pilot pending,
yêu cầu video bổ sung và không training hoặc gán ép cho đủ nhãn.

Người dùng gán hai lượt; lượt hai sau ít nhất 24 giờ, xáo thứ tự và không hiện
nhãn lượt một. Đây là kiểm tra nhất quán của một người, không giả gọi là độ
đồng thuận nhiều annotator. Nếu có người thứ hai, báo riêng kết quả người đó.
Gate: ít nhất 18/20 đoạn đồng ý bộ nhãn có mặt, tính unclear với uncertain_labels
và không hành động rõ ràng, không gộp hai nhóm này. Ghép event một-một theo
nhãn và tay quan sát được bằng matching một-một có tổng temporal IoU lớn nhất;
không so UUID nhóm giữa hai lượt, hai unknown chỉ ghép bằng thời gian trong
cùng đoạn pilot. Khi hòa chọn thứ tự start_frame rồi end_frame. IoU là giao/hợp các
frame inclusive. Ít nhất 80% event rõ của mỗi lượt có đối tác IoU >=0.5, event
không ghép tính trượt. Báo sai lệch crossing_frame trên cặp hand event, mục
tiêu ít nhất 90% cặp lệch <=2 frame; không có cặp thì gate chưa đạt. Các con số
là ngưỡng pilot cho guideline v1, chưa là độ chính xác được nghiệm thu.

Ghi các trường hợp disagreement, sửa guideline rồi đánh giá lại toàn bộ pilot
hai lượt theo version mới, giữ lịch sử. Không đổi ngưỡng chỉ để biến fail thành
pass. Kiểm thao tác: sau khi đã xác định frame cần chọn, tối thiểu 9/10 thao
tác nhập nhãn hoàn thành bằng phím tắt trong <=15 giây mỗi nhãn; ghi thời gian
xem/quyết định nhãn riêng. Không claim tốc độ gán nhãn trên footage dài từ phép
đo nhập liệu này. Gate M2 là công cụ + guideline, không là độ chính xác model.

### M3 — dataset snapshot và bàn giao local

Export annotations-only và portable, kiểm số event/revision/ROI/index/hash
khớp DB snapshot, đọc lại bằng schema validator độc lập. Test confirmed,
unclear, background, unreviewed và split leakage. Mô phỏng lỗi copy/disk:
không có READY/export partial. Fixture ít nhất ba nhóm recording độc lập kiểm
split; test hai excerpt hash khác cùng parent, clip qua nửa đêm, metadata
unknown, đổi split so với manifest trước. Test concurrent edit trong export
và crash giữa publish/READY. Shop thật thiếu ngày nghiệm thu export unsplit;
đánh giá model trên real train/test là coverage bổ sung sau V2, không gate phần
mềm bị treo vô hạn. Không gọi unsplit là dataset sẵn sàng đo tổng quát hóa.

Các fixture bắt buộc cho mask: A=take_out confirmed, B=unclear cùng thời gian
→ giữ positive A; hand_in confirmed + take_out unclear cùng nhóm → giữ hand_in,
ignore take_out; review riêng hand_in → không tạo negative take_out; sửa event
[10,20] thành [30,40] → invalidates coverage ở cả hai đoạn; sửa ROI → training
bị chặn, archive vẫn đọc được. Mỗi fixture được kiểm round-trip schema và
assert giá trị mask cụ thể, không chỉ kiểm file tồn tại.

Chạy luồng thật import → ROI → gán nhãn → review → export → mở lại. Xác minh
shortcut trên Desktop người dùng nhìn thấy trỏ đúng repository V2, launcher
chỉ sở hữu service V2 và không tái sử dụng PID V1. Dùng launcher hiện có làm
một nguồn logic, không fork toàn bộ script chỉ để đổi tên. Không ghi đè shortcut
V1 của người dùng. Cập nhật hướng dẫn cách mở, gán nhãn và giữ bản export.

### Gate chung

Chạy test bị ảnh hưởng, toàn bộ backend/frontend và production build theo
handoff; browser thật cho mọi workflow UI. CUDA thật áp dụng khi dùng person
tracking và xác nhận baseline tracking còn hoạt động; không cần GPU để tuyên
bố lưu annotation đúng. V1 gate 125-frame và media giữ nguyên; nếu thay pipeline
ngoài dự kiến thì phải dừng, đánh giá lại phạm vi và chạy gate vision liên quan.
Ghi commands/kết quả/limitations, không đi tiếp khi gate bắt buộc đang pending.

## 10. Giữ chất lượng và phạm vi riêng tư

Chỉ làm tại `C:\Users\Admin\Documents\ChatGPT\CCTV AI` trên nhánh
`codex/v2-offline-person-tracking`; giữ tag và worktree V1. Không remote,
push/publish, host, upload video/labels hoặc telemetry. Loopback, UUID lookup,
resolved-path checks áp dụng cả frame service, annotation và export.

Không technical debt là gate có thể kiểm tra: một nguồn contracts/config,
migration có test và rollback dữ liệu đã thiết kế, lưu có revision, không
hard-code ROI/ngưỡng gợi ý, không silent fallback, không bỏ failing tests,
không commit dataset thật. Backup local trước migration; failure giữ dữ liệu
cũ đọc được bởi phiên bản trước, không nửa migration. Không refactor legacy
không liên quan chỉ để làm V2.

AI gợi ý theo person box chưa nằm trong M1–M3: person box gần rổ không đủ xác
định tay tương tác. Khi bổ sung model/dataset công khai, lưu suggestion tách
ground truth, đo lợi ích trên tập shop giữ riêng và chỉ nhận vào nhãn sau review.
V2.1 không còn được dùng để chỉ toàn bộ V2; M1–M3 trong tài liệu này là thứ tự
triển khai chính thức sau khi bản viết được người dùng review.

## 11. Kết luận audit revision 2

Hợp đồng đã được chỉnh cho interval/crossing, nhóm tay, uncertainty theo lớp,
coverage/mask, recording provenance, frame service, snapshot export và lưu
đồng thời. Quy tắc cụ thể và trường dữ liệu nằm trong các mục 3–9; audit record
truy vết phát hiện tới mục sửa và gate kiểm tra, không thay thế spec.

Chưa có evidence rằng người dùng luôn nhìn được tâm tay/vật ở góc camera này.
Pilot M2 phải xác nhận guideline; nếu không phân biệt được hai chuyển động,
giữ unclear và sửa guideline cùng người dùng, không thêm model để che vấn đề.
Các gate hiệu năng, độ nhất quán và export mới là yêu cầu, chưa được chạy.
Không tuyên bố hết defect hoặc không bao giờ cần migration; chỉ chuyển sang
milestone sau khi gate bắt buộc của milestone hiện tại qua.
