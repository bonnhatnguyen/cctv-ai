# V2 — công cụ gán nhãn hành động quanh rổ tiền

Ngày: 2026-09-10. Trạng thái: bản đặc tả để người dùng review trước kế hoạch
triển khai. Người dùng đã chọn công cụ native trong ứng dụng, một ROI rổ tiền
cố định và năm nhãn `hand_in`, `hand_out`, `take_out`, `put_in`, `unclear`.
Các lựa chọn kỹ thuật dưới đây cụ thể hóa hướng đã duyệt; chưa có code V2 mới.

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
bởi tài liệu này. Chưa thực thi plan cũ. Sửa copy ID được gom vào milestone UI
liên quan; shortcut còn thiếu được đưa vào gate bàn giao.

## 2. Kết quả người dùng nhận được

Trong ứng dụng CCTV AI cục bộ, người dùng mở clip, khoanh rổ, xem chậm hoặc bước
từng frame, chọn khoảng bắt đầu/kết thúc và bấm một trong năm nhãn. Có thể sửa,
xóa nhầm rồi khôi phục, mở lại phiên gán nhãn, review và xuất dataset riêng tư.
Mỗi nhãn cho biết đoạn nào trong video nguồn đã được xem và kết luận gì từ hình
ảnh. V2 hoạt động được ngay khi chưa có model hành động hoặc person tracking.

V2 không cần public dataset để vận hành. Training, auto-label, pose, nhận diện
tiền, nhập dataset ngoài, RTSP, vai trò người bán/khách và cảnh báo là các hạng
mục riêng sau này. Ngôn ngữ gọn trên UI; cấu trúc dữ liệu chi tiết nằm phía sau.

## 3. Từ điển nhãn và quy tắc xem video

ROI khoanh phần rổ quan sát được, gồm miệng rổ; người dùng dùng cùng quy tắc
khoanh giữa các clip của cùng góc camera. ROI chỉ là vùng ảnh 2D. Nó không chứng
minh tay/vật ở bên trong rổ theo chiều sâu.

| Nhãn | Bằng chứng cần thấy | Khoảng frame được đánh dấu |
| --- | --- | --- |
| hand_in | Tay nhìn thấy chuyển từ ngoài vào vùng rổ | Frame cuối thấy tay ở ngoài đến frame đầu thấy tay ở trong |
| hand_out | Tay nhìn thấy chuyển từ vùng rổ ra ngoài | Frame cuối thấy tay ở trong đến frame đầu thấy tay ở ngoài |
| take_out | Thấy vật ở rổ rồi được tay mang ra | Từ bắt đầu thao tác lấy vật nhìn thấy đến khi vật ra ngoài rõ ràng |
| put_in | Thấy vật được tay mang đến và đặt vào rổ | Từ bắt đầu đưa vật vào đến khi nhìn thấy hoàn tất đặt vật |
| unclear | Có tương tác quanh rổ nhưng bị che hoặc thiếu bằng chứng để phân loại | Khoảng tương tác không thể xác định |

Quy ước vào/ra: tâm phần bàn tay nhìn thấy nằm trong/ngoài polygon; nằm đúng
đường biên được tính trong. Không cần vẽ điểm bàn tay hay chạy pose trong V2.
Chỉ gán hand_in/out khi nhìn được chuyển tiếp; nếu khó xác định tâm hoặc hướng,
dùng unclear. Tay đứng yên trong ROI không tạo hand_in lặp lại. Hai tay có thể
tạo hai event; chọn `hand=left|right|unknown` khi nhận biết được.

Take_out/put_in cần thấy thao tác với vật, không chỉ chuyển động tay qua vùng.
Chỉ chọn `object=cash` khi thấy đó là tiền; rổ tiền không đủ để tự điền cash.
Thao tác hand_in và put_in có thể chồng thời gian: đây là dataset đa nhãn.
Không tự sinh nhãn tay từ nhãn vật hoặc ngược lại.

`unclear` không phải mẫu âm. Nó có thể chồng thời gian với nhãn khác nếu thuộc
tay/người khác; với cùng thao tác, chỉ giữ phần chưa xác định, tránh hai kết
luận mâu thuẫn. Mọi đoạn chưa review cũng chưa phải mẫu âm.

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
phục và đánh dấu nhãn cùng review coverage cần review lại. Chặn xuất confirmed
cho đến khi người dùng xác nhận lại theo ROI mới.

## 5. Thời gian và frame chính xác

Nguồn sự thật là `start_frame`/`end_frame` zero-based, inclusive, thỏa
0 <= start <= end < decoded_frame_count. Thời gian hiển thị được dẫn xuất từ
frame và fps_num/fps_den của nguồn CFR; không lưu một cặp timestamp độc lập có
thể trôi khỏi frame. Tôn trọng định dạng V1: MP4 CFR và kích thước chẵn.

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

## 6. Bản ghi và trạng thái dữ liệu

Hợp đồng backend typed là nguồn sự thật. Sinh TypeScript types từ schema được
kiểm tra trong build, không viết hai bộ enum action độc lập.

| Bản ghi | Nội dung chính |
| --- | --- |
| Clip | UUID, source hash, source job UUID, frame count, fps phân số, kích thước/SAR, recording_day do người dùng xác nhận, camera setup |
| ROI revision | UUID, setup, revision, polygon, clip binding, thời điểm tạo |
| Annotation | UUID, clip UUID, ROI revision, label, start/end frame, hand, object, visibility, optional track reference, review state |
| Revision | revision number, previous revision, payload, operator local, UTC save time, thao tác create/edit/delete/restore/review |
| Review coverage | Khoảng frame đã xem kỹ, gắn ROI revision và revision tập nhãn đã review |
| Dataset export | UUID, schema version, revision manifest, split mapping, hashes, trạng thái xuất |

`object=cash|other|unknown`; hand_in/out và unclear mặc định unknown, không ép
người dùng đoán. `visibility=clear|occluded`; thiếu hoàn toàn bằng chứng của
tương tác được biểu diễn bằng unclear, không thêm nhãn suy đoán.
`review_state=draft|confirmed|needs_review`; deleted là revision tombstone,
không xóa lịch sử. Confirmed nghĩa người dùng đã review: một unclear confirmed
vẫn là nhãn không xác định, không thành hành động dương/âm.

Track reference là `(tracking_job_id, local_track_id)` tùy chọn, được xác minh
thuộc cùng nguồn. V1 ID không phải khóa danh tính; ID đổi có thể để trống hoặc
chia event khi thật sự phù hợp. Không bắt buộc tracking lại để gán nhãn; không
merge/re-identify người trong V2.

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

SQLite annotation là nơi lưu chính thức, nằm trong `data/v2-annotations/`
được Git-ignore trước khi có dữ liệu. JSON export là snapshot bất biến, không
là DB thứ hai cần đồng bộ. Các cache frame và staging export ở cùng private
root, không vào Git. Nhãn thật cũng là dữ liệu riêng tư, không chỉ video.

Lưu event cùng revision trong một transaction; UI có nút Lưu và trạng thái
đang lưu/đã lưu/lỗi. Không báo đã lưu trước phản hồi server. Gửi expected
revision để phát hiện sửa ở hai tab; HTTP 409 giữ bản nháp cho người dùng so
sánh/tải lại, không overwrite thầm lặng. Dùng operation UUID cho retry cùng
request để không sinh event trùng. Review lưu revision cụ thể; sửa nhãn hủy
review coverage của khoảng bị ảnh hưởng và đưa nhãn về draft.

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
provenance và hash nguồn. Hai chế độ rõ ràng: annotations-only hoặc portable
gồm bản sao video gốc cùng relative paths. Bản portable vẫn hoàn toàn local;
copy và kiểm hash, không move nguồn. Không tự cắt/re-encode training samples.

Train/validation/test được chọn theo recording_day do người dùng xác nhận,
không suy từ thời gian import. Một ngày chỉ ở một split; cùng source hash và
mọi excerpt chung parent recording không được chia sang split khác. Khi chưa
có đủ ngày, cho xuất unsplit, ghi rõ chưa sẵn sàng đánh giá model. Export split
thiếu day/provenance đủ để kiểm tra rò rỉ phải bị từ chối.

Confirmed action được xuất với nhãn; unclear thành vùng ignore. Khoảng review
đã hoàn tất không có hành động/unclear là ứng viên background. Đoạn chưa xem,
draft hoặc needs_review là vùng exclude. Không tự lấy toàn bộ phần còn lại
trong clip làm negative. Chặn xuất training khi còn vùng cần review do sửa ROI.

Ghi ra staging UUID, xác minh tất cả file/hash và manifest rồi publish thư mục
snapshot hoàn chỉnh. Lỗi không xuất link READY, không ghi đè snapshot cũ.
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

### M2 — năm nhãn, chỉnh sửa và review

Chọn khoảng, tạo/sửa/xóa/khôi phục đủ năm nhãn; hand/object/visibility và track
reference tùy chọn; revision conflict/retry được test bằng hai tab. Reload và
restart giữ revision đã lưu; chặn interval ngoài frame range; overlap hợp lệ
được giữ. Sửa ROI hoặc event làm mất review tương ứng đúng quy tắc.

Nghiệm thu cùng người dùng trên clip shop quanh rổ: thử ít nhất một ví dụ mỗi
loại thực sự nhìn thấy và một vùng che khuất. Nếu thiếu ví dụ của một loại,
ghi gate lớp đó pending và xin clip bổ sung; không dán nhãn ép cho đủ năm loại.
Ghi lại disagreement để sửa guideline trong cùng milestone. Không claim độ
chính xác model vì M2 là công cụ thủ công.

### M3 — dataset snapshot và bàn giao local

Export annotations-only và portable, kiểm số event/revision/ROI/index/hash
khớp DB snapshot, đọc lại bằng schema validator độc lập. Test confirmed,
unclear, background, unreviewed và split leakage. Mô phỏng lỗi copy/disk:
không có READY/export partial. Fixture ít nhất ba ngày kiểm split; shop thật
thiếu ngày chỉ được nghiệm thu unsplit, phần split thực ghi pending.

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

## 11. Tự review đặc tả

- Chỉ một subsystem mới: annotation quanh một ROI; ba milestone đều có luồng
  người dùng kiểm thử độc lập.
- Frame/index, ROI revisions, overlaps, unknown/negative, track reference,
  persistence conflicts và export leakage đã có nghĩa cụ thể.
- Phân biệt quyết định đã duyệt, đề xuất kỹ thuật và bằng chứng còn cần clip.
- Không cam kết training/accuracy, schema không bao giờ migration hoặc zero
  defect; mọi thay đổi tương lai cần version và gate phù hợp.
