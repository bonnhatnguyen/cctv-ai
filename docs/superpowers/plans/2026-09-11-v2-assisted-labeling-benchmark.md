# V2 Assisted Labeling — Chặng A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây công cụ benchmark local, read-only để so MediaPipe và Grounding DINO trên cùng đoạn video/ROI, tạo proposal `hand_in/out` và đo chất lượng cùng công sức review trước khi chọn model tích hợp V2.

**Architecture:** Package CLI riêng `app.annotation_benchmark` đọc snapshot có version từ hai DB hiện tại mà không khởi tạo service/migration. Hai detector adapter dùng chung crop/frame schedule, association và evaluator; kết quả là artifact private bất biến, không là annotation V2. Không gọi model từ API/frontend và không thay môi trường ứng dụng.

**Tech Stack:** Windows PowerShell, Python 3.12, SQLite read-only, FFmpeg RGB24 sequential decode, Pydantic v2, NumPy/Pillow, MediaPipe Tasks, PyTorch/Transformers, pytest; dependencies model trong venv riêng.

**Spec:** `docs/superpowers/specs/2026-09-11-v2-assisted-labeling-design.md`. Kế thừa ngữ nghĩa frame/ROI/nhãn từ `docs/superpowers/specs/2026-09-10-v2-basket-action-annotation-design.md`.

## Global Constraints

- Chỉ chặng A. Chặng B UI/API/persistence proposal cần kết quả A và người dùng duyệt model cùng mục tiêu chất lượng trước plan riêng.
- Không đánh số lại V1/V2 hoặc M1–M3; không thay plan M2/M3. Pilot M2 và full real-browser workflow còn pending, không được tick thay bằng benchmark.
- `hand_in`, `hand_out` là hai nhãn proposal duy nhất; label null cho đoạn cần xem. Không tự gán `put_in`, `take_out`, cash hoặc `unclear`.
- Frame nguồn zero-based inclusive; polygon chuẩn hóa raster nguồn, biên tính trong; estimate crossing không phải annotation exact-frame đã review.
- Grounding DINO dùng prompt `hand.`; không dùng person box V1 làm tay. MediaPipe handedness score không phải detection/event confidence.
- Không nối tay qua mất dấu/association mơ hồ để tạo crossing. ID chỉ local theo run/clip, không là người hoặc trái/phải giải phẫu.
- Không migration, mutation API, tạo/sửa/xác nhận event/coverage, sửa legacy hand tracker, hoặc thay môi trường `.venv`/V1 đang chạy.
- Không upload video/nhãn, telemetry, remote inference, push/publish; model download chỉ trong setup rõ ràng. Inference offline và fail nếu weights/dependency thiếu.
- Video nguồn, labels, manifest và báo cáo chi tiết private, Git-ignored; không thêm bản sao video/PNG/masks mặc định.
- Config/run/model revision + weights hashes bất biến. Không dùng floating `main` cho lần đo; không silent fallback model/device.
- Không chọn ngưỡng nghiệm thu chất lượng thay người dùng. Không đủ reference/coverage thì metric liên quan unavailable/PENDING_DATA, không 0 hoặc PASS giả.

## Phạm vi file và cách chạy

Working directory cho lệnh Python/test bên dưới: `backend/`.
`../.venv-assist-benchmark/Scripts/python.exe` là interpreter benchmark mới.
Lệnh Git và setup PowerShell chạy ở root repository.

| File tạo mới | Trách nhiệm |
| --- | --- |
| `backend/app/annotation_benchmark/__init__.py` | Package nhẹ, không load model/service khi import |
| `backend/app/annotation_benchmark/contracts.py` | Manifest/config/frame/observation/proposal/report schemas |
| `backend/app/annotation_benchmark/snapshot.py` | Hai DB read-only, binding và snapshot reference |
| `backend/app/annotation_benchmark/media.py` | Source integrity, sequential decode, crop/SAR/timestamp |
| `backend/app/annotation_benchmark/models.py` | Manifest weights, kiểm hash và detector protocol |
| `backend/app/annotation_benchmark/mediapipe_adapter.py` | MediaPipe Tasks local, reset theo segment |
| `backend/app/annotation_benchmark/dino_adapter.py` | Grounding DINO local, prompt cố định |
| `backend/app/annotation_benchmark/proposals.py` | Association bảo thủ + ROI transitions |
| `backend/app/annotation_benchmark/metrics.py` | Matching, coverage, metric và effort summary |
| `backend/app/annotation_benchmark/runner.py` | Chạy theo segment/model, deadline, artifact/quota |
| `backend/app/annotation_benchmark/cli.py` | `freeze`, `validate`, `run`, `evaluate` |
| `backend/tests/annotation_benchmark/conftest.py` | DB/media fixtures synthetic độc lập |
| `backend/tests/annotation_benchmark/test_*.py` | Tests tương ứng từng task bên dưới |
| `scripts/setup-assisted-benchmark.ps1` | Chỉ setup venv mới, lock dependency và weights manifest |
| `scripts/assisted-benchmark-requirements.in` | Dependency đầu vào riêng, không đổi pyproject runtime |
| `scripts/assisted-benchmark-requirements.lock.txt` | Versions/hashes được resolve và smoke-test khi thực thi Task 3 |
| `docs/v2-assisted-benchmark-runbook.md` | Quy trình setup/freeze/run/review/evaluate và phục hồi lỗi |

Files sửa: `.gitignore` thêm chính xác `.venv-assist-benchmark/`;
`docs/v2-acceptance.md` thêm ledger chặng A; `docs/v2-user-guide.md` thêm trạng
thái CLI thử nghiệm, không quảng cáo có nút UI chưa tồn tại. Các schema/runtime
V2 không sửa. Fixture DB tự tạo trong test temp dir, không đọc dữ liệu thật.

Output root: `data/v1/m1-acceptance/annotations/benchmarks/` cho lần chạy shop
hiện tại; tổng quát dùng `<annotation_root>/benchmarks/` đã kiểm binding.
Models/cache setup nằm trong `data/v1/assisted-models/`. Root này được ignore
bởi quy tắc `data/v1/` hiện có. Không ghi asset/model/video vào Git.

---

### Task 1: Hợp đồng và snapshot read-only

**Files:** Create `contracts.py`, `snapshot.py`, `__init__.py`,
`backend/tests/annotation_benchmark/conftest.py`, `test_snapshot.py`,
`test_contracts.py`; Modify `.gitignore`.

**Interfaces:**

```python
# contracts.py: Pydantic, extra='forbid', frozen=True, strict frame integers.
class FrameSpan(BaseModel):
    start_frame: int
    end_frame: int

class SelectionItem(BaseModel):
    segment_id: UUID
    clip_id: UUID
    span: FrameSpan
    partition: Literal['tuning', 'evaluation', 'exploratory']
    parent_recording_id: str | None
    recording_days: tuple[str, ...]
    provenance_confirmed: bool
    scenario_tags: tuple[str, ...]

class ReferenceEvent(BaseModel):
    event_id: UUID
    label: Literal['hand_in', 'hand_out']
    span: FrameSpan
    crossing_frame: int
    revision: int

class FrozenSegment(BaseModel):
    selection: SelectionItem
    source_job_id: UUID
    source_sha256: str
    clip_revision: int
    roi_revision_id: UUID
    polygon: tuple[tuple[float, float], ...]
    guideline_version: Literal[1]
    frame_count: int
    width: int
    height: int
    fps_num: int
    fps_den: int
    sar_num: int
    sar_den: int
    events: tuple[ReferenceEvent, ...]
    coverage: dict[str, tuple[FrameSpan, ...]]
    ignored: dict[str, tuple[FrameSpan, ...]]

class FrozenManifest(BaseModel):
    schema_version: Literal[1]
    manifest_id: UUID
    frozen_at: datetime
    segments: tuple[FrozenSegment, ...]
    reference_state: Literal['ready', 'pending_data']
    missing_scenarios: tuple[str, ...]

# snapshot.py
def read_snapshot(source_root: Path, annotation_root: Path,
                  selection: list[SelectionItem]) -> FrozenManifest: ...
```

- [ ] Thêm ignore trước setup. Ghi baseline HEAD/status và `pip freeze` của `.venv` vào evidence private; không ghi package inventory vào nhãn DB.
- [ ] Viết RED tests với fixtures tạo bằng schema hiện tại: read-only khi app owner lock đang giữ, sai binding, version mới, source job khác clip, source path traversal/symlink/junction ra ngoài root, ROI cũ, source thiếu/hash sai, duplicate/overlap segment, unknown provenance, read-only không tạo DB thiếu.

```python
def test_snapshot_never_bootstraps_database(private_fixture):
    before = private_fixture.logical_dump()
    with private_fixture.fail_on_bootstrap():
        snap = read_snapshot(private_fixture.source_root,
                             private_fixture.annotation_root,
                             private_fixture.selection)
    assert private_fixture.logical_dump() == before
    assert snap.segments[0].roi_revision_id == private_fixture.roi_id
```

`private_fixture` tạo hai DB temp theo schema app, một source synthetic và
ROI/event/coverage; `logical_dump()` sort tất cả rows và schema; context
`fail_on_bootstrap()` monkeypatch `AnnotationDatabase.initialize`,
`create_schema` và repository mutations để raise. Fixture không mock SQLite
read-only enforcement: thêm test `INSERT` trên connection thật bị từ chối.

- [ ] Chạy `../.venv/Scripts/python.exe -m pytest tests/annotation_benchmark/test_snapshot.py tests/annotation_benchmark/test_contracts.py -q`; kỳ vọng fail do package/hợp đồng chưa tồn tại.
- [ ] Implement kết nối URI `path.resolve().as_uri() + '?mode=ro'`, `uri=True`, `PRAGMA query_only=ON`; read transaction ngắn. Không `immutable=1` trên WAL DB đang hoạt động, không `AnnotationDatabase.initialize`, `FrameService._verify_source` (có mutation), không đổi journal mode.
- [ ] Adapter đọc schema v2 qua `schema_migrations`, `annotation_binding`, `annotation_clips`, `roi_revisions`, `action_annotations`, `review_coverage`; V1 `jobs.db/v1_tracking_jobs` cho source_path. Parse media bằng `MediaView`, polygon bằng validator hiện có. Version khác 2 bị từ chối; test schema adapter với DB do migration thật tạo để bắt drift.
- [ ] Source path phải bằng `<source_root>/jobs/<source_job_id>/source.mp4` sau resolve, nằm trong root, hash khớp; source metadata khớp snapshot. Hai DB không atomic: read V2 snapshot, lookup V1 nguồn bất biến, kiểm lại binding/source job/ROI trước trả; thay đổi thì fail snapshot, không retry đoán.
- [ ] Chỉ snapshot confirmed/current ROI/guideline, không deleted; giữ ignored spans từ unclear/draft/needs_review theo lớp. Active coverage đúng ROI/guideline, trừ ignored khi đánh giá âm; snapshot không tự tạo coverage. Event bị cắt bởi segment không đưa vào positive đầy đủ, phần bị cắt thành ignored. Không suy hand events từ put_in/take_out.
- [ ] Kiểm recording_days/shared source hash/parent_recording_id theo connected components: nhóm không đi qua tuning/evaluation. Unknown provenance chỉ exploratory; cùng clip segments không overlap để tránh đếm trùng. Required scenario tags là `entry`, `exit`, `stationary`, `near_pass`, `boundary_jitter`, `multiple_hands`, `occlusion`, `no_action`; thiếu tag hoặc chưa đủ reference thì pending, không fake nhãn.
- [ ] `reference_state=ready` chỉ khi có ít nhất một confirmed reference mỗi hand class, scenario selection đủ và coverage hợp lệ cho các đoạn đối chứng từng lớp; đây là đủ để tính mô tả, không đủ để nghiệm thu chất lượng. Tag `no_action` không thay coverage; sai/thiếu attestation giữ pending. Freeze xuất canonical JSON + SHA-256, `validate/run` kiểm checksum; chạy với config khác tạo run khác, chỉnh reference phải freeze manifest mới và ghi evaluation không còn blind nếu đã xem dự đoán.
- [ ] GREEN tests; commit riêng `feat(v2): add read-only assisted benchmark snapshots` chỉ các file task. Không stage `data/`.

### Task 2: Frame chính xác, crop có context và transform SAR

**Files:** Create `media.py`, `backend/tests/annotation_benchmark/test_media.py`; extend `contracts.py`.

**Interfaces:**

```python
class RunConfig(BaseModel):
    schema_version: Literal[1] = 1
    stride: int = 1
    margin_ratio: float = 0.5
    context_ms: int = 1000
    max_input_side: int = 960
    association_distance: float = 0.15
    ambiguity_margin: float = 0.03
    boundary_epsilon: float = 0.002
    min_side_samples: int = 2
    motion_epsilon: float = 0.002
    dino_box_threshold: float = 0.25
    dino_text_threshold: float = 0.25
    mp_detection_threshold: float = 0.5
    mp_presence_threshold: float = 0.5
    mp_tracking_threshold: float = 0.5
    max_hands: int = 4
    device: Literal['cpu', 'cuda:0'] = 'cpu'
    segment_deadline_seconds: int = 600
    decoder_stall_seconds: int = 30
    output_quota_bytes: int = 536870912
    free_disk_reserve_bytes: int = 2147483648

@dataclass(frozen=True)
class CropTransform:
    left: int
    top: int
    right: int   # exclusive, source raster
    bottom: int  # exclusive
    input_width: int
    input_height: int
    source_width: int
    source_height: int
    def source_xy(self, x: float, y: float) -> tuple[float, float]: ...

@dataclass(frozen=True)
class SampledFrame:
    source_index: int
    timestamp_ms: int
    rgb: np.ndarray
    transform: CropTransform

def model_timestamp_ms(index: int, fps_num: int, fps_den: int,
                       previous_ms: int | None) -> int: ...
def iter_source_frames(source: Path, segment: FrozenSegment,
                       config: RunConfig) -> Iterator[SampledFrame]: ...
```

Config defaults là điểm bắt đầu thử nghiệm, không gate chất lượng và không
được tune trên evaluation. Validate positive integers/bounded floats,
thresholds [0,1], finite, margin >0, min_side_samples >=2. SAR và transform
metadata luôn ghi vào output, không derive lại bằng kích thước UI.

- [ ] Viết RED tests numbered synthetic MP4 SAR 1:1/2:1, index đầu/giữa/cuối, 30000/1001 fps, stride, crop chạm biên, tiny ROI, timestamp collisions, short decoder read, source hash đổi, cancellation/deadline. Tạo helper fixture riêng theo pattern annotation/conftest, không import fixture test module khác.

```python
def test_source_timestamps_and_crop_roundtrip():
    assert model_timestamp_ms(30, 30000, 1001, None) == 1001
    assert model_timestamp_ms(1, 2000, 1, 0) == 1
    t = CropTransform(100, 50, 300, 150, 400, 100, 1000, 500)
    assert t.source_xy(0.5, 0.5) == (0.2, 0.2)
```

- [ ] Chạy `../.venv/Scripts/python.exe -m pytest tests/annotation_benchmark/test_media.py -q`; xác nhận lỗi vì chức năng chưa có.
- [ ] Implement FFmpeg pipe sequential từ đầu source, không approximate seek/proxy/overlay, `-fps_mode passthrough`, RGB24 và bounded stderr drain; đếm decoder index trước sampling. Decode full source một lượt mỗi segment, chỉ giữ sampled crop trong RAM, kiểm frame count và bytes/frame; không lưu full frame list. Ghi decode riêng để nhận ra chi phí này khi đánh giá.
- [ ] Crop rectangle mở rộng mỗi phía `margin_ratio * roi_width/height`, floor/ceil và clamp raster. Resize thành square-pixel input theo SAR rồi fit max_input_side không letterbox; source_xy ánh xạ normalized input qua crop về normalized source. Giữ polygon không thay đổi và dùng công thức này cho cả hai adapter.
- [ ] Timestamp `max(floor(index*1000*fps_den/fps_num), previous_ms+1)` khi có previous; lưu cả index/fps và timestamp đã lượng tử hóa. Reset previous theo segment; schedule `start + k*stride <= end` và context decode khi cần nhưng không infer ở ngoài selection đã chốt.
- [ ] Khi generator đóng/lỗi/cancel, terminate/wait owned subprocess trong deadline và đóng pipes; Windows subprocess ẩn. Không gọi release/reprepare hoặc đọc derived generations. Source hash trước/sau full run phải bằng frozen hash; mismatch là fail, không sửa DB.
- [ ] GREEN unit/media tests so RGB selected indices với decoder nguồn độc lập; commit `feat(v2): add exact-frame benchmark media sampling`.

### Task 3: Hai model adapter và môi trường có thể tái lập

**Files:** Create `models.py`, `mediapipe_adapter.py`, `dino_adapter.py`,
`scripts/setup-assisted-benchmark.ps1`, `scripts/assisted-benchmark-requirements.in`,
`scripts/assisted-benchmark-requirements.lock.txt`, `test_models.py`, `test_model_smoke.py`;
extend `contracts.py`.

**Interfaces:**

```python
class ModelAsset(BaseModel):
    model_id: str
    revision: str
    files_sha256: dict[str, str]  # relative paths only
    license_source: str
    license_sha256: str

class Observation(BaseModel):
    frame_index: int
    bbox: tuple[float, float, float, float]  # source normalized xyxy
    score: float | None
    score_kind: Literal['grounding_score', 'handedness', 'unavailable']

class Detector(Protocol):
    def detect(self, frame: SampledFrame) -> list[Observation]: ...
    def close(self) -> None: ...

def load_detector(name: Literal['mediapipe', 'dino'], model_root: Path,
                  asset: ModelAsset, config: RunConfig) -> Detector: ...
def verify_asset(model_root: Path, asset: ModelAsset) -> None: ...
```

- [ ] Viết RED mocked SDK tests: MediaPipe Tasks list-of-landmarks/list-of-categories thật về shape, không `.landmark`/`.classification` legacy; timestamp nguồn; score semantics; empty result; dtype/channel; malformed NaN/out-of-range box; close; DINO cố định prompt; không network call hoặc auto fallback.

```python
def test_asset_hash_mismatch_is_not_empty_detection(tmp_path):
    model = tmp_path / 'hand_landmarker.task'
    model.write_bytes(b'wrong-test-weights')
    asset = ModelAsset(model_id='mediapipe-hand-landmarker', revision='test-v1',
                       files_sha256={'hand_landmarker.task': '0' * 64},
                       license_source='local-test', license_sha256='0' * 64)
    with pytest.raises(ValueError, match='hash'):
        verify_asset(tmp_path, asset)
```

- [ ] RED tests chạy bằng `.venv` không cần model imports; lazy import adapters để core tests không đòi ML packages.
- [ ] Viết setup PowerShell chỉ target `.venv-assist-benchmark`, xác minh resolved path trong repo, không `--system-site-packages`, không pip vào `.venv`. Venv đã có chỉ validate/reuse, không force recreate. Setup tách `-DownloadModels` khỏi tạo venv; in size/disk và nguồn trước download, không khởi động backend.

```powershell
# Root repository; bên trong script dùng & và mảng args, kiểm $LASTEXITCODE.
& ./.venv/Scripts/python.exe -m venv .venv-assist-benchmark
& ./.venv-assist-benchmark/Scripts/python.exe -m pip install pip-tools
& ./.venv-assist-benchmark/Scripts/python.exe -m piptools compile --generate-hashes --output-file scripts/assisted-benchmark-requirements.lock.txt scripts/assisted-benchmark-requirements.in
& ./.venv-assist-benchmark/Scripts/python.exe -m pip install --require-hashes -r scripts/assisted-benchmark-requirements.lock.txt
```

Input deps là `pydantic>=2,<3`, `numpy`, `Pillow`, `pytest`, `mediapipe`,
`torch`, `transformers`, `huggingface-hub`, `safetensors`, `psutil`, `scipy`.
Trong execution, kiểm official API/platform compatibility rồi resolve exact
lock trên Python 3.12/Windows, smoke-test trước commit lock. Không invent
versions/hashes trong plan hoặc gắn lock chưa chạy là verified. Setup tool
versions cũng ghi vào private environment manifest; ghi index/wheel hashes,
Torch/CUDA build và ffmpeg version. CUDA không khả dụng thì run cấu hình CUDA
fail preflight; muốn CPU phải tạo config/run CPU rõ ràng.

- [ ] Setup tải DINO từ official model repo, resolve commit SHA trước snapshot_download; MediaPipe lấy versioned official model URL từ guide, ghi URL/content SHA và license. Không trust_remote_code, không executable pickle từ repo lạ; DINO dùng safetensors. Manifest path traversal bị chặn. Record exact file hashes, GPU/CPU và pip freeze sau setup. Model manifest private, không commit weights.
- [ ] Adapter MediaPipe: Tasks VIDEO, local BaseOptions, config thresholds/max_hands, `detect_for_video`; bbox min/max landmarks thực trả, transform về source. `score_kind=handedness` nếu có score, thiếu thì null/unavailable, không hằng số giả 0.75. Bbox center dùng chung proposal layer.
- [ ] Adapter DINO: local processor/model từ snapshot, `local_files_only=True`, inference/eval mode, `hand.`; config thresholds, processor target size crop rồi normalize/mapping. Dùng explicit device, score_kind grounding_score. Malformed detections trả lỗi rõ, không coi là no-hand. Không đưa model IDs khác vào config tự do.
- [ ] Chạy core tests GREEN và smoke opt-in `../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation_benchmark/test_model_smoke.py -q --model-root ../data/v1/assisted-models`. Register `--model-root` tại conftest; không có flag thì skip có lý do, gate thực tế bắt buộc flag. Smoke trên synthetic kiểm load/inference/output/close chứ không đòi phát hiện tay giả. Shop chất lượng chỉ ở Task 6.
- [ ] Commit `feat(v2): add isolated pretrained hand detector adapters`; không sửa backend pyproject, frontend lock hoặc runtime weights.

### Task 4: Association bảo thủ và proposal theo ROI

**Files:** Create `proposals.py`, `backend/tests/annotation_benchmark/test_proposals.py`; extend `contracts.py`.

**Interfaces:**

```python
class Proposal(BaseModel):
    proposal_id: UUID
    segment_id: UUID
    local_track_id: int | None
    label: Literal['hand_in', 'hand_out'] | None
    action_span: FrameSpan | None
    view_span: FrameSpan
    crossing_estimate: int | None
    crossing_bracket: FrameSpan | None
    reason: Literal['crossing', 'boundary', 'track_gap', 'association', 'clip_boundary']

def point_in_roi(point: tuple[float, float],
                  polygon: tuple[tuple[float, float], ...]) -> bool: ...
def build_proposals(segment: FrozenSegment,
                    observations: list[tuple[int, list[Observation]]],
                    config: RunConfig) -> list[Proposal]: ...
```

- [ ] Viết RED test matrix: outside→inside, inside→outside, đứng yên, điểm trên edge/vertex, jitter, near pass không crossing, hai tay giao nhau, output order đảo, missed sample, clip cut, sampled crossing bracket, motion đảo chiều, nhiều proposal chồng thời gian không merge mất tay.

```python
def test_boundary_is_inside_but_absence_is_not_outside():
    roi = ((0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6))
    assert point_in_roi((0.4, 0.5), roi)
    assert not point_in_roi((0.39, 0.5), roi)

def test_hand_reappearing_inside_has_no_direction(segment_fixture):
    observations = segment_fixture.observations([
        (10, [(0.3, 0.5)]), (11, []), (12, [(0.5, 0.5)])])
    proposals = build_proposals(segment_fixture.segment, observations, RunConfig())
    assert not any(p.label in {'hand_in', 'hand_out'} for p in proposals)
```

Fixture method tạo normalized bbox tâm được cho, cùng kích thước, score null;
segment có ROI [0.4,0.6] và 100 frames. Không code fixture dựa nhãn cần đo.

- [ ] Chạy `../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation_benchmark/test_proposals.py -q` RED.
- [ ] Implement cùng association cho cả hai adapter: sort boxes lexicographic, tính center distance source normalized; chỉ nối mutual-nearest unique trong association_distance và cách ứng viên thứ hai ít nhất ambiguity_margin ở cả hai chiều. Không ghép còn lại kiểu greedy; ambiguous tạo review-only, reset track. Thiếu ở một scheduled sample kết thúc track ngay; không carry identity qua gap. Reset mọi segment.
- [ ] Chuyển trạng thái với min_side_samples quan sát liên tiếp mỗi phía; observation sát biên trong boundary_epsilon giữ pending, không cộng sự kiện. Lưu bracket từ quan sát phía nguồn gần nhất tới quan sát phía đích đầu tiên; estimate là frame đích đầu tiên, luôn ghi estimated, không confirmed. Boundary epsilon tính source normalized point-to-segment, khác point-in-polygon định nghĩa biên.
- [ ] Tách hai khoảng: action_span lấy từ bắt đầu chuyển động liên tục trước crossing đến dừng/đổi hướng sau crossing (chuyển động <=motion_epsilon coi đứng yên, đổi hướng theo dot product <0), view_span thêm context_ms đổi sang frame bằng ceil(fps*ms/1000), clamp selection. Nếu không thấy đầu/cuối chuyển động do cut/gap chỉ label null, reason tương ứng. Không dùng view_span làm action_span để làm đẹp IoU.
- [ ] Proposal IDs deterministic UUID5 từ segment/config hash/track/interval/reason; sort theo frame rồi ID, giữ đa tay/đa nhãn chồng lấp. Không auto tạo interaction/annotation hoặc suy độ tin cậy event từ detector score. Detector/proposal không đọc events/coverage/reference_state trong FrozenSegment; test thay toàn bộ reference mà observations giữ nguyên phải cho proposal y hệt, ngăn rò nhãn đối chứng vào thuật toán.
- [ ] GREEN tests gồm permutation invariance và sample gaps; commit `feat(v2): propose reviewable ROI hand transitions`.

### Task 5: Evaluator tách coverage, localization và nhãn

**Files:** Create `metrics.py`, `backend/tests/annotation_benchmark/test_metrics.py`; extend `contracts.py`.

**Interfaces:**

```python
class EffortRecord(BaseModel):
    segment_id: UUID
    operator: str
    mode: Literal['manual', 'assisted']
    model_run_id: UUID | None
    order: int
    elapsed_seconds: float
    outside_proposal_review_seconds: float
    outside_review_completed: bool
    completed: bool

def temporal_iou(a: FrameSpan, b: FrameSpan) -> Fraction: ...
def match_events(reference: list[ReferenceEvent], proposals: list[Proposal]
                 ) -> list[tuple[UUID, UUID, Fraction]]: ...
def evaluate(manifest: FrozenManifest, proposals: list[Proposal],
              effort: list[EffortRecord]) -> dict: ...
```

- [ ] RED tests zero denominator, unreviewed proposals not FP, ignored uncertain class, overlapping coverage union, partial coverage, true positives trong unclear ROI khác nhóm, duplicate proposals, unmatched events, same-label-only matching, ties/permutation, crossing lỗi lớn, sample view recall nhưng action IoU thấp, missing effort, thiếu held-out.

```python
def test_inclusive_iou_and_zero_overlap():
    assert temporal_iou(FrameSpan(start_frame=10, end_frame=20),
                        FrameSpan(start_frame=20, end_frame=30)) == Fraction(1, 21)
    assert temporal_iou(FrameSpan(start_frame=10, end_frame=20),
                        FrameSpan(start_frame=21, end_frame=30)) == 0

def test_unreviewed_is_not_false_positive(metric_fixture):
    report = evaluate(metric_fixture.no_coverage_manifest,
                      metric_fixture.unmatched_proposals, [])
    assert report['classes']['hand_in']['precision'] is None
    assert report['classes']['hand_in']['unscored_proposals'] == 1
    assert report['effort']['savings_ratio'] is None
```

- [ ] Chạy `../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation_benchmark/test_metrics.py -q` RED.
- [ ] Implement matching từng segment/label không dùng model track ID để match human interaction. Chỉ edge IoU>=0.5, tối đa tổng exact Fraction IoU; tie-break sorted reference start/end/ID rồi proposal start/end/ID. Dùng min-cost flow với chi phí `-Fraction(IoU)`, capacity=1, Bellman-Ford shortest augmenting path cho đến khi không còn cải thiện tổng chi phí. Để chốt tie lexicographic, cố định lần lượt edge theo thứ tự chuẩn chỉ nếu residual optimum giữ nguyên tổng optimum; test đối chiếu exhaustive n<=5. Không copy exponential bitmask matcher M2; không dùng floating epsilon để thay exact tie contract.
- [ ] TP là matched reviewed positives, FN là reference không matched. Unmatched proposal chỉ FP khi toàn action_span ở valid coverage lớp đó sau trừ ignored; còn lại unscored. Precision=TP/(TP+FP) báo rõ subset, thêm evaluated/unscored counts và duration; denominator zero null. FP/minute dùng union fully reviewed evaluable spans của lớp, đổi bằng fps phân số. Không cộng thời lượng chồng trùng.
- [ ] Localization recall là tỷ lệ reference crossings trong union view_spans mọi proposal (kể cả label null), không đòi label đúng; báo riêng event precision/recall. Report raw matched crossing/start/end absolute errors, median/p95 nearest-rank khi có cặp và FP/FN counts luôn hiện.
- [ ] Report per-partition và per-class, reference counts, missing scenarios, excluded/unknown duration, descriptive-only nếu exploratory. Không report chung tuning+evaluation như held-out. Metric pending không chuyển thành pass do process exit 0.
- [ ] Effort: elapsed_seconds bao gồm tìm/xem/sửa/xác nhận và rà ngoài proposal; outside_proposal_review_seconds là phần con, không cộng hai lần. Manual có model_run_id null, assisted bắt buộc UUID run đang evaluate; không trộn effort hai model. Chỉ so completed records trong schedule đối chứng, cùng operator; thiếu hai mode, time không hợp lệ hoặc outside_review_completed=false thì savings null + pending reason. Giá trị outside-review zero được phép nếu không có vùng ngoài proposal, không dùng zero thay attestation. Chưa đo thì không suy tiết kiệm từ inference FPS.
- [ ] GREEN tests với exact brute-force oracle n<=5, adversarial matching/tie cases và kiểm scale fixture lớn; commit `feat(v2): evaluate assisted labels without false negatives`.

### Task 6: CLI, run artifacts, thử thật và gate chọn model

**Files:** Create `runner.py`, `cli.py`, `backend/tests/annotation_benchmark/test_runner.py`,
`test_cli.py`, `docs/v2-assisted-benchmark-runbook.md`; Modify `docs/v2-acceptance.md`,
`docs/v2-user-guide.md`.

**Interfaces:**

```python
def run_benchmark(manifest: FrozenManifest, config: RunConfig,
                   model_name: Literal['mediapipe', 'dino'],
                   asset: ModelAsset, source_root: Path,
                   annotation_root: Path, model_root: Path) -> Path: ...
def main(argv: list[str] | None = None) -> int: ...
```

CLI input selection JSON là `{schema_version:1, segments:[SelectionItem...]}`;
`freeze` đọc snapshot và tạo manifest immutable trong private root, không gọi
đó là M3 dataset export. Config/asset/effort dùng schemas Task 1–5. UUIDs
do freeze/run tạo được stdout trả làm input bước sau, không hard-code ID shop.

```powershell
# Root: setup, chỉ khi thực thi plan, không chạy trong lượt viết plan.
./scripts/setup-assisted-benchmark.ps1 -DownloadModels
# backend/: sau khi người dùng chọn đoạn trong selection.json private.
../.venv-assist-benchmark/Scripts/python.exe -m app.annotation_benchmark.cli freeze --source-root ../data/v1/m1-acceptance --annotation-root ../data/v1/m1-acceptance/annotations --selection ../data/v1/m1-acceptance/annotations/benchmarks/selection.json
```

`validate --manifest FILE --config FILE --model-root DIR` kiểm reference/config/assets;
`run --manifest FILE --config FILE --model mediapipe|dino --source-root DIR
--annotation-root DIR --model-root DIR` trả run UUID/output path;
`evaluate --run DIR --effort FILE` tạo report mới không overwrite;
`--effort` optional và thiếu thì effort pending. Chỉ nhận local files;
không URL source/output arbitrary, output luôn dưới bound benchmark root.

- [ ] RED integration tests end-to-end fake adapter; immutable output collision; missing weights/OOM/decode error; Ctrl+C/deadline; process death artifact partial; stale ROI/source/guideline before publish; quota; second run concurrent; symlink output escape; exit codes; DB unchanged. Test network blocked trong subprocess inference, không mock toàn bộ detect path.

```python
def test_failure_never_publishes_completed_run(runner_fixture):
    result = runner_fixture.run_child(detector_error='out_of_memory')
    assert result.returncode == 2
    assert runner_fixture.published_reports() == []
    assert runner_fixture.failed_status()['reason'] == 'out_of_memory'
    assert runner_fixture.annotation_dump() == runner_fixture.before_dump
```

- [ ] Chạy focused runner/CLI tests RED.
- [ ] Runner lock one benchmark run per root với Windows file lock; không tranh GPU: preflight V1 running job thì báo bận, yêu cầu chạy benchmark khi V1 tracking idle. Không tự stop service/kill process không sở hữu. Ghi giới hạn này vì CLI không có shared scheduler của chặng B.
- [ ] Parent process chạy model child + FFmpeg owned tree với deadline/cancel và cleanup; dùng Windows Job Object kill-on-close hoặc process ownership tương đương đã test (không `taskkill` theo tên). Child crash/lỗi fail run, không publish zero-event success. Observation stream JSONL private có quota, không giữ full video RAM; proposal processing per segment. Một model một run, cùng frozen manifest và common config trừ device/model thresholds được report.
- [ ] Output staging UUID chứa input hashes/config/assets/environment, sampled frame indices/transforms, observations/proposals, progress và resource metrics. Default quota 512 MiB benchmark outputs với reserve 2 GiB; cache/weights tính riêng và setup báo size. Không auto xóa run cũ hoặc source để đủ quota. Finish kiểm hashes/binding lại rồi atomic rename không overwrite. Partial run chỉ status interrupted/failed, retry UUID mới.
- [ ] Đo cold model load/decode/inference/postprocess/end-to-end riêng; synchronize CUDA trước/sau timed inference; MP CPU và DINO GPU được ghi đúng, không gọi so tốc độ thuần. Peak GPU memory/RAM unavailable nếu collector không hỗ trợ, không zero. Các scheduled frame phải có observations kể cả empty; thiếu frame là fail hoặc explicit partial, không PASS.
- [ ] Runbook hướng dẫn chọn/tag đoạn trước prediction, freeze reference, unknown provenance exploratory, review thủ công ở workspace V2 hiện có (người dùng thao tác, CLI không ghi nhãn). Xuất danh sách view spans dạng Markdown private kèm clip/frame để người dùng mở bằng control sẵn có; không thêm web server/player mới.
- [ ] Runbook effort schedule: chọn các đoạn tương đương đã freeze, phân hai nhóm manual-first/assisted-first, đổi thứ tự lượt sau, record operator/order/elapsed/outside-review/completed; công khai hiệu ứng nhớ khi xem cùng footage. Ghi cả effort chưa thực hiện. Không chờ giả 24h hoặc thay pilot M2 bằng schedule này.
- [ ] GREEN fake/integration tests. Chạy cả hai adapter weights thật trên cùng frozen shop selection. Không cần đủ nhãn để technical smoke nhưng phải log thiếu reference và không đoán `hand_in/out` từ put_in hiện có. Nếu data chưa đủ: hoàn tất software gate, ghi quality/effort PENDING_DATA và danh sách cụ thể cần người dùng review.
- [ ] Chạy regression và build, capture exit codes thật:

```powershell
# backend/, môi trường benchmark
../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation_benchmark -q
# backend/, runtime ứng dụng giữ nguyên; exclude benchmark có optional deps
../.venv/Scripts/python.exe -m pytest tests --ignore=tests/annotation_benchmark -q
../.venv/Scripts/python.exe -m compileall -q app/annotation_benchmark
# frontend/
pnpm test -- --run
pnpm build
# root/
git diff --check
git status --short
```

So pip freeze runtime trước/sau và source/annotation logical snapshot khi app
không có user mutation đồng thời. Nếu user đang sửa, không dùng byte DB change
làm chứng benchmark đã ghi; test read-only enforcement và audit transaction
là bằng chứng chính. Không boot app trong benchmark tests để tránh side effect.

- [ ] Ledger ghi riêng SOFTWARE, MODEL_SMOKE (từng adapter), QUALITY, EFFORT;
  model smoke có thể fail/pending độc lập software. Không ghi PASS chung nếu
  thiếu gate. Báo số liệu tổng hợp và lỗi điển hình, không commit private reports.
- [ ] Self-review diff theo spec A; commit `feat(v2): add private assisted-label benchmark runner` sau software verification. Nếu model smoke bị môi trường chặn, ghi BLOCKED_ENV và không tuyên bố đã benchmark model.
- [ ] Bàn giao kết quả và khuyến nghị một trong: tích hợp MediaPipe, tích hợp DINO,
  thử SAM2 vì tracking yếu, hoặc chưa tích hợp. Xin duyệt model/quality target
  trước viết plan B. Không bắt đầu training/export/UI trong plan này.

## Plan self-review và mapping spec

| Yêu cầu spec | Task/gate |
| --- | --- |
| Vị trí V2, không thay M2/M3, không giả hoàn tất pilot | Global, 6 |
| Pretrained comparison/local model isolation/provenance | 3, 6 |
| Snapshot clip/hash/ROI/coverage, freeze trước predictions | 1, 6 |
| Group split/no leakage/thiếu dữ liệu | 1, 5 |
| Exact decode/SAR/context/source timestamps | 2 |
| Không misuse handedness confidence/identity | 3, 4 |
| Crossings có evidence, gap/ambiguity, estimated interval | 4 |
| Localization/event metrics, partial coverage, effort | 5, 6 |
| Quota, no-video-copy, offline/failure/cancel | 2, 3, 6 |
| Benchmark không bootstrap migration/mutate legacy/runtime | 1, 3, 6 |
| Software tests khác model smoke/quality | 3, 6 |
| Chặng B schema/API/UI/worker persistence | Cố ý ngoài phạm vi; gate cuối 6 |

Tất cả checkbox execution để trống ở lượt lập plan. Tên và signatures dùng
chung giữa task; ellipsis trong interface blocks biểu thị contract Python,
không phải lời hứa chức năng đã implement. Dependency lock/model revision là
artifact cần tạo và verify ở Task 3, không có giá trị bịa sẵn. Các con số config
là defaults đo thử, không accuracy gate đã duyệt. Không delegate self-review.
