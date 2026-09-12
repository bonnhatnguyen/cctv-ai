# V2 Model-Assisted Review Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đưa Grounding DINO/MediaPipe vào workspace V2 như một hàng đợi proposal có provenance để người dùng review, sửa và tự lưu annotation, không auto-label hoặc làm yếu workflow thủ công.

**Architecture:** FastAPI chỉ quản lý job/proposal metadata trong annotation DB; model chạy bằng Python của `.venv-assist-benchmark` trong một subprocess owned, dùng lại adapter/media/proposal core đã benchmark. UI poll run, hiển thị queue riêng và đưa proposal vào editor hiện có; transaction tạo annotation đồng thời claim suggestion để không có trạng thái nửa vời.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic v2, subprocess/Windows Job Object, Grounding DINO Tiny, MediaPipe Tasks, React theo lockfile hiện có, TypeScript, Vitest/Testing Library, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-v2-assisted-review-integration-design.md`

**Revision:** 2026-09-12 — sửa 8 findings audit. Đây là plan chưa thực thi;
checkbox và các ví dụ test không phải bằng chứng tính năng đã pass.

**Working directories:** Lệnh pytest chạy tại `backend/`; lệnh pnpm tại
`frontend/`; lệnh Git và generator chạy ở root worktree. Fixture/helper mới trong
ví dụ phải được implement trong test module hoặc `tests/annotation/conftest.py`
trước RED; không tính NameError/fixture-not-found là RED hợp lệ.

## Global Constraints

- Đây là assisted review thử nghiệm; không gọi proposal là ground truth, không tự xác nhận annotation và không tự tạo coverage.
- Model mặc định là `dino`; registry chỉ nhận `dino` hoặc `mediapipe`, không nhận path/module/class từ HTTP.
- API runtime không import Torch, Transformers hoặc MediaPipe; mọi model dependency ở `.venv-assist-benchmark`.
- Chỉ một assistance run chạy cùng lúc; không silent fallback model/device/weights và không chạy khi V1 tracking đang bận.
- Frame là zero-based inclusive; model estimate/bracket luôn được hiển thị là ước lượng.
- Source hash, ROI revision, guideline, config và asset hash là binding bất biến của run; stale chặn publish/accept.
- Proposal, annotation và review coverage là ba dữ liệu tách biệt; reject không tạo negative và accept không tạo coverage.
- Artifact ở private annotation root, có quota/reserve, không lưu bản sao video/PNG/mask mặc định.
- Manual annotation vẫn hoạt động khi model chưa cài, lỗi, OOM hoặc bị tắt.
- QUALITY và EFFORT giữ `PENDING_DATA`; hoàn tất UI không nâng hai gate này.

## File map

| File | Trách nhiệm |
| --- | --- |
| `backend/app/annotation/contracts.py` | OpenAPI contracts cho model/run/suggestion và provenance khi tạo action |
| `backend/app/annotation/database.py` | Schema v3 và migration v2→v3 |
| `backend/app/annotation_benchmark/snapshot.py` | Reader read-only tương thích schema 2 và 3 |
| `backend/app/inference_lease.py` | Lease độc quyền process-safe cho V1, assistance và CLI benchmark |
| `backend/app/owned_process.py` | Extract process-tree helper từ benchmark runner, không import model |
| `backend/app/v1/worker.py` | Giữ lease trong suốt xử lý tracking |
| `backend/app/annotation_benchmark/runner.py` | Dùng chung lease và process helper |
| `backend/app/annotation/assistance_store.py` | CRUD/job state, stale checks, atomic suggestion claim |
| `backend/app/annotation/assistance_protocol.py` | Request/result file schema giữa API process và model child |
| `backend/app/annotation/assistance_child.py` | Entry point model-only; không mở DB hoặc web API |
| `backend/app/annotation/assistance_worker.py` | Queue, process ownership, deadline, cancel, publish/recovery |
| `backend/app/annotation/api.py` | HTTP routes và domain-error mapping |
| `backend/app/annotation/settings.py` | Interpreter/model/artifact limits tập trung |
| `backend/app/v1/api.py` | Compose/start/stop assistance service với app lifecycle |
| `frontend/src/annotation/AssistedReviewPanel.tsx` | Range/run/progress/queue/reject/use-draft UI |
| `frontend/src/annotation/ActionWorkspace.tsx` | Nối proposal vào ActionEditor và refresh queue |
| `frontend/src/annotation/ActionEditor.tsx` | Banner provenance/estimated fields |
| `frontend/src/annotation/api.ts` | Typed calls cho assistance API |
| `frontend/src/annotation/types.generated.ts` | Types sinh từ backend OpenAPI |
| `scripts/generate-annotation-types.py` | Đăng ký DTO mới trong MODELS và sinh JSON schema/TypeScript từ Pydantic |
| `frontend/src/styles.css` | Layout/status cho panel, không thay visual system chung |

---

### Task 1: Schema v3 và contracts proposal tách biệt

**Files:**
- Modify: `backend/app/annotation/contracts.py`
- Modify: `backend/app/annotation/database.py`
- Modify: `backend/app/annotation_benchmark/snapshot.py`
- Test: `backend/tests/annotation_benchmark/test_snapshot.py`
- Create: `backend/app/annotation/assistance_store.py`
- Test: `backend/tests/annotation/test_assistance_database.py`
- Test: `backend/tests/annotation/test_assistance_store.py`

**Interfaces:**
- Produces: `AssistanceRepository`, `AssistanceModelId`, `AssistanceModelInfo`, `AssistanceRunCreate`, `AssistanceRunView`, `AssistanceSuggestionView`, `SuggestionReject`.
- Produces: `AssistanceRepository.create_run()`, `get_run()`, `get_suggestion()`, `list_runs()`, `run_binding()`, `next_queued_run()`, `mark_running()`, `publish()`, `fail()`, `request_cancel()`, `list_suggestions()`, `reject_suggestion()`.
- Consumes: `AnnotationDatabase`, `annotation_clips`, `roi_revisions`, operation-log conventions hiện có.

- [ ] **Step 1: Viết RED migration tests**

```python
def test_v2_database_migrates_to_v3_without_losing_annotations(v2_database):
    before = v2_database.dump_user_rows()
    migrated = reopen_with_current_schema(v2_database.root)
    assert migrated.schema_version() == 3
    assert migrated.dump_user_rows() == before
    assert migrated.table_names() >= {"assistance_runs", "assistance_suggestions"}

def test_failed_v3_migration_rolls_back_v2_database(v2_database):
    before = v2_database.dump_user_rows()
    with pytest.raises(RuntimeError, match="migration"):
        reopen_with_current_schema(v2_database.root, before_version_write=raising_migration)
    assert reopen_with_v2_reader(v2_database.root).dump_user_rows() == before
```

- [ ] **Step 2: Chạy migration tests và xác nhận RED đúng lý do**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_database.py -q`

Expected: FAIL vì `SCHEMA_VERSION == 2` và chưa có hai bảng assistance.

- [ ] **Step 3: Implement schema v3/migration tối thiểu**

Thêm method `AnnotationDatabase._migrate_v3(connection)` theo pattern `_migrate_v2`;
chạy trong `BEGIN IMMEDIATE` hiện có, cập nhật version sau toàn bộ DDL. Test dùng
hook `_before_version_write` hiện có để inject lỗi; rollback transaction phải giữ
nguyên logical dump/version v2. Backup tạo bằng SQLite backup API trước upgrade,
không tự overwrite DB bằng backup khi exception. Kiểm cả new DB 0→3 và 1→3.

Tăng `SCHEMA_VERSION = 3`. `assistance_runs` có unique `(clip_id, operation_id)`,
frame checks và state checks. `assistance_suggestions` có unique `(run_id,
proposal_key)`, nullable label/action/crossing fields, `review_state`,
`accepted_annotation_id` và foreign keys. Index queue theo
`(clip_id, review_state, view_start_frame, id)`; không thêm column vào bảng
annotation ở task này.

- [ ] **Step 4: Chạy migration tests GREEN**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_database.py -q`

Expected: PASS; transaction rollback giữ nguyên DB v2 khi injected failure xảy ra.

- [ ] **Step 4a: RED/GREEN reader benchmark trên DB đã migrate**

```python
@pytest.mark.parametrize("schema_version", [2, 3])
def test_snapshot_supports_annotation_schema(schema_version, snapshot_fixture):
    snapshot_fixture.create_schema(schema_version)
    snapshot = snapshot_fixture.read()
    assert snapshot.segments[0].source_sha256 == snapshot_fixture.source_sha256
```

Tạo fixture DB v2 và DB v3 có cùng source/ROI/action/coverage. Chạy test bằng env
benchmark, xác nhận schema 3 fail vì check `!= 2`; sửa allowlist thành `{2, 3}`,
giữ unknown future version bị từ chối. Thêm test freeze/run/evaluate trên v3,
reader không migrate hoặc mutate DB và raw suggestion không vào reference.
Run: `../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation_benchmark/test_snapshot.py -q`.

- [ ] **Step 5: Viết RED contracts/store tests**

```python
def test_create_run_is_idempotent_and_freezes_binding(store, ready_clip):
    request = AssistanceRunCreate(
        operation_id=UUID("00000000-0000-0000-0000-000000000021"),
        expected_clip_revision=ready_clip.revision,
        model="dino", start_frame=10, end_frame=80,
    )
    first = store.create_run(ready_clip.id, request)
    second = store.create_run(ready_clip.id, request)
    assert second.id == first.id
    assert (first.source_sha256, first.roi_revision_id, first.guideline_version) == (
        ready_clip.source_sha256, ready_clip.roi.id, 1,
    )

def test_empty_publish_is_success_not_negative(store, queued_run):
    store.mark_running(queued_run.id)
    result = store.publish(queued_run.id, expected_binding=store.run_binding(queued_run.id), proposals=[])
    assert result.status == "succeeded"
    assert store.list_suggestions(queued_run.clip_id).items == []
```

Also test invalid/out-of-range interval, no ROI, unprepared/source unavailable,
unknown model, duplicate operation different payload, invalid state transition,
ordered pagination, nullable label, deterministic proposal key collision and
stale binding before publish.

- [ ] **Step 6: Chạy store tests RED**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_store.py -q`

Expected: FAIL import `app.annotation.assistance_store`.

- [ ] **Step 7: Implement contracts và `AssistanceRepository`**

Contract lõi:

```python
AssistanceModelId = Literal["dino", "mediapipe"]
AssistanceRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
SuggestionReviewState = Literal["pending", "accepted", "rejected", "stale"]

class AssistanceRunCreate(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)
    model: AssistanceModelId
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)

class AssistanceRunView(Dto):
    id: UUID
    clip_id: UUID
    model: AssistanceModelId
    status: AssistanceRunStatus
    start_frame: int
    end_frame: int
    processed_frames: int
    scheduled_frames: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    source_sha256: str
    roi_revision_id: UUID
    guideline_version: int
    device: Literal["cpu", "cuda:0"]
    config_sha256: str
    asset_sha256: str

class AssistanceSuggestionView(Dto):
    id: UUID
    run_id: UUID
    clip_id: UUID
    label: Literal["hand_in", "hand_out"] | None
    action_start_frame: int | None
    action_end_frame: int | None
    view_start_frame: int
    view_end_frame: int
    crossing_estimate: int | None
    crossing_bracket_start: int | None
    crossing_bracket_end: int | None
    reason: Literal["crossing", "boundary", "track_gap", "association", "clip_boundary"]
    review_state: SuggestionReviewState
    accepted_annotation_id: UUID | None

class AssistanceModelInfo(Dto):
    model: AssistanceModelId
    available: bool
    device: Literal["cpu", "cuda:0"]
    error_code: str | None

class AssistanceRunListView(Dto):
    items: list[AssistanceRunView]
    next_cursor: str | None

class AssistanceSuggestionListView(Dto):
    items: list[AssistanceSuggestionView]
    next_cursor: str | None

class SuggestionReject(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)

class AssistanceRunCancel(Dto):
    operation_id: UUID
```

Model-list response là `list[AssistanceModelInfo]`. Bổ sung interval validator
`end_frame >= start_frame`; clip bound được store kiểm với media.frame_count.
Tách `run_binding(run_id)` nội bộ khỏi public DTO; sửa test `queued_run.binding`
thành `store.run_binding(queued_run.id)`. Store có `list_runs(clip_id, active_only,
limit, cursor)` để khôi phục UI; chọn ordering `(created_at,id)` ổn định, limit 1–100.

Store methods mở transaction ngắn, kiểm clip/ROI/source binding tại create và
publish, canonicalize request cho operation replay, map row→Pydantic view và
không import package model.

Clip revision dùng optimistic concurrency cho user mutations; inference chỉ
stale khi source/ROI/guideline đổi. User lưu nhãn hoặc tạo lượt tay trong lúc
inference không làm bỏ kết quả hợp lệ. Test riêng hai trường hợp revision đổi
do annotation và đổi do ROI. Pending suggestions được đánh stale khi đọc/list/
claim dưới kiểm tra binding; không sửa accepted/rejected history.

- [ ] **Step 8: Chạy focused GREEN và regression DB**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_database.py tests/annotation/test_assistance_store.py tests/annotation/test_database.py -q`

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```powershell
git add backend/app/annotation/contracts.py backend/app/annotation/database.py backend/app/annotation/assistance_store.py backend/tests/annotation/test_assistance_database.py backend/tests/annotation/test_assistance_store.py backend/app/annotation_benchmark/snapshot.py backend/tests/annotation_benchmark/test_snapshot.py
git commit -m "feat(v2): persist model assistance runs and proposals"
```

---

### Task 2: Claim proposal nguyên tử khi lưu annotation

**Files:**
- Modify: `backend/app/annotation/contracts.py`
- Modify: `backend/app/annotation/repository.py`
- Modify: `backend/app/annotation/assistance_store.py`
- Test: `backend/tests/annotation/test_repository.py`
- Test: `backend/tests/annotation/test_assistance_store.py`

**Interfaces:**
- Consumes: `ActionAnnotationCreate`, `AssistanceRepository.claim_for_annotation(connection, ...)`.
- Produces: optional `suggestion_id` trên create request; annotation response không bị biến thành proposal response.

- [ ] **Step 1: Viết RED atomic-accept tests**

```python
def test_create_action_claims_suggestion_in_same_transaction(repo, store, pending_suggestion):
    result = repo.create_action(
        pending_suggestion.clip_id,
        valid_action_create(suggestion_id=pending_suggestion.id),
    )
    created = result.annotations[-1]
    suggestion = store.get_suggestion(pending_suggestion.id)
    assert suggestion.review_state == "accepted"
    assert suggestion.accepted_annotation_id == created.id

def test_action_validation_failure_leaves_suggestion_pending(repo, store, pending_suggestion):
    with pytest.raises(ValueError):
        repo.create_action(
            pending_suggestion.clip_id,
            invalid_action_create(suggestion_id=pending_suggestion.id),
        )
    assert store.get_suggestion(pending_suggestion.id).review_state == "pending"
```

Add cases: suggestion from another clip, failed/stale run, rejected/already
accepted proposal, ROI changed after run, retry same operation, operation ID
reused with another suggestion, overlapping annotation and reject idempotency.

- [ ] **Step 2: Chạy RED**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_repository.py tests/annotation/test_assistance_store.py -q`

Expected: FAIL vì `ActionAnnotationCreate` chưa nhận `suggestion_id`.

- [ ] **Step 3: Implement claim helper và hook đúng transaction**

```python
class ActionWrite(ActionFields):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)

class ActionAnnotationCreate(ActionWrite):
    suggestion_id: UUID | None = None

class ActionAnnotationUpdate(ActionWrite):
    expected_annotation_revision: StrictInt = Field(ge=1)

def claim_for_annotation(
    connection: sqlite3.Connection,
    *, suggestion_id: UUID,
    clip_id: UUID,
    annotation_id: UUID,
    source_sha256: str,
    roi_revision_id: UUID,
    guideline_version: int,
    now: str,
) -> None: ...
```

Trong `create_action`, chạy validation action trước, INSERT annotation rồi gọi
`claim_for_annotation` và commit trong cùng `write_transaction`.
Claim dùng conditional UPDATE `WHERE review_state='pending'` sau khi kiểm run
succeeded/binding; rowcount khác 1 thành `SuggestionConflict`. Operation replay
hiện có chạy trước claim nên lost response không claim lần hai. Nếu claim thất
bại, rollback cả annotation, revision, coverage invalidation và operation log.
Không tắt foreign_keys hoặc đổi sang deferred FK để né sai thứ tự.

Test hai connection đồng thời accept cùng suggestion: chỉ một annotation được
commit. Test injected failure sau INSERT: không còn annotation mồ côi, suggestion
vẫn pending. Test Update từ chối `suggestion_id` (422), Create thủ công vẫn tương
thích; tạo action có thể invalidate coverage cũ theo logic hiện có, không tạo
coverage mới. Assertion “coverage không đổi” chỉ đúng fixture không có coverage
liên quan; không vô hiệu hóa quy tắc invalidation hiện tại.

- [ ] **Step 4: Chạy GREEN và toàn repository tests**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_repository.py tests/annotation/test_assistance_store.py tests/annotation/test_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```powershell
git add backend/app/annotation/contracts.py backend/app/annotation/repository.py backend/app/annotation/assistance_store.py backend/tests/annotation/test_repository.py backend/tests/annotation/test_assistance_store.py backend/tests/annotation/test_contracts.py
git commit -m "feat(v2): accept model suggestions through annotation drafts"
```

---

### Task 3: Isolated model-child protocol và owned worker lifecycle

**Files:**
- Create: `backend/app/annotation/assistance_protocol.py`
- Create: `backend/app/annotation/assistance_child.py`
- Create: `backend/app/annotation/assistance_worker.py`
- Modify: `backend/app/annotation/settings.py`
- Create: `backend/app/inference_lease.py`
- Create: `backend/app/owned_process.py`
- Modify: `backend/app/v1/worker.py`
- Modify: `backend/app/annotation_benchmark/runner.py`
- Test: `backend/tests/integration/test_inference_lease.py`
- Test: `backend/tests/annotation/test_assistance_protocol.py`
- Test: `backend/tests/annotation/test_assistance_worker.py`

**Interfaces:**
- Consumes: `AssistanceRepository`, `FrameService.resolve_source_path()`, benchmark adapters/media/proposals.
- Produces: `AssistanceWorker.start()`, `stop()`, `wake()`, `cancel(run_id)`, `reconcile_startup()`.
- File protocol: `AssistanceChildRequest` JSON input and `AssistanceChildResult` JSON output with schema version 1.

- [ ] **Step 1: Viết RED protocol tests**

```python
def test_child_request_rejects_arbitrary_model_and_paths(tmp_path):
    with pytest.raises(ValidationError):
        AssistanceChildRequest.model_validate({
            "schema_version": 1, "model": "module:evil", "source": "https://host/x.mp4"
        })

def test_child_result_requires_every_scheduled_frame(result_payload):
    result_payload["scheduled_frames"] = [10, 15, 20]
    result_payload["observed_frames"] = [10, 20]
    with pytest.raises(ValidationError, match="scheduled"):
        AssistanceChildResult.model_validate(result_payload)
```

Request chứa only resolved source path do parent tạo, source SHA, raster/SAR,
ROI polygon/revision, inclusive interval, model asset directory và immutable
config. Validator nhận local absolute path, nhưng child không nhận dữ liệu HTTP.

- [ ] **Step 2: Chạy protocol RED**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_protocol.py -q`

Expected: FAIL import module mới.

- [ ] **Step 3: Implement schemas và child entry point tối thiểu**

```python
def run_child(request_path: Path, result_path: Path) -> int:
    request = AssistanceChildRequest.model_validate_json(request_path.read_bytes())
    detector = load_registered_adapter(request.model, request.model_asset, request.device)
    observations = observe_exact_schedule(request, detector)
    proposals = build_proposals(request.segment(), observations, request.run_config)
    atomic_write_json(result_path, AssistanceChildResult.from_run(request, observations, proposals))
    return 0
```

Child không mở annotation DB, không sửa source và không tự download. Empty
observations từng scheduled frame vẫn được ghi; thiếu frame/lỗi detector trả
non-zero và không tạo success result.

- [ ] **Step 4: Chạy protocol GREEN**

Run: `../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation/test_assistance_protocol.py -q`

Expected: PASS trong env model tách biệt.

- [ ] **Step 5: Viết RED worker lifecycle tests với fake child executable**

```python
def test_worker_publishes_only_complete_bound_result(worker_fixture):
    run = worker_fixture.enqueue(start=10, end=30)
    worker_fixture.fake_child.complete(valid_result(run))
    worker_fixture.worker.run_once()
    assert worker_fixture.store.get_run(run.id).status == "succeeded"
    assert worker_fixture.store.list_suggestions(run.clip_id).items

def test_model_failure_never_becomes_empty_success(worker_fixture):
    run = worker_fixture.enqueue()
    worker_fixture.fake_child.exit_code = 2
    worker_fixture.worker.run_once()
    failed = worker_fixture.store.get_run(run.id)
    assert failed.status == "failed"
    assert failed.error_code == "model_process_failed"
```

Add cases: missing interpreter/asset, tracking busy, child hang/deadline, cancel
queued/running, shutdown, process-tree ownership, OOM error mapping, malformed or
partial result, source/ROI stale before publish, output quota/reserve, clip
switch independence and startup reconciliation.

- [ ] **Step 6: Chạy worker RED**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_worker.py -q`

Expected: FAIL import `AssistanceWorker`.

- [ ] **Step 7: Implement settings và worker**

Settings mới, có validator path tuyệt đối:

```python
assistance_enabled: bool = True
assistance_python: Path | None = None
assistance_model_root: Path | None = None
assistance_dino_device: Literal["cpu", "cuda:0"] = "cuda:0"
assistance_stride: int = 5
assistance_queue_limit: int = 4
assistance_deadline_seconds: float = 900
assistance_poll_seconds: float = 0.25
assistance_output_limit_bytes: int = 512 * 1024**2
assistance_max_frames: int = 1800
```

`AssistanceWorker` dùng event/thread giống `PreparationWorker`, nhưng launch
subprocess qua `app.owned_process` được extract từ benchmark runner; cả CLI và
assistance dùng chung helper và tests kill-on-close/suspended-start/resume.
Worker không giữ DB transaction khi chờ child. Progress file chỉ cập nhật
processed/scheduled counters. Cancel đóng owned Job Object và publish cancelled;
không `taskkill` theo tên/PID không xác minh.

`None` cho interpreter/model root nghĩa là assistance unavailable; API vẫn boot
và manual mode hoạt động. Khi đã cấu hình, kiểm absolute path; missing files,
missing package hoặc CUDA unavailable được preflight bằng child ngắn có timeout
và trả availability code, không raise xuyên app startup. MediaPipe dùng CPU;
DINO dùng explicit setting, không tự fallback. Default stride=5 cùng RunConfig
benchmark còn lại được freeze/hash tại enqueue; không đọc lại config mới lúc chạy.
Queue limit kiểm atomically trong transaction; queued/running đếm vào limit.

- [ ] **Step 7a: RED/GREEN lease chung cho V1, assistance và benchmark CLI**

```python
def test_tracking_waits_while_assistance_owns_device(lease_fixture):
    with lease_fixture.acquire("assistance"):
        tracking = lease_fixture.start_tracking_in_another_process()
        assert not tracking.entered_inference.wait(timeout=0.2)
    assert tracking.entered_inference.wait(timeout=5)
```

`InferenceLease.acquire(cancel_requested)` là context manager dùng Windows byte
lock (POSIX flock cho test hỗ trợ), khóa ở user-local runtime directory chung
mọi source root/worktree; không dùng lock riêng từng annotation root. Serialize
cả CPU/GPU inference để giữ policy đơn giản. V1 `_process_one`, assistance
child lifecycle và benchmark `run_benchmark` phải acquire trước model load và
release sau model/child cleanup; V1 đang xếp hàng đợi lease vẫn cancellable.
Chỉ owner của lease được chuyển job sang processing/running. Test hai thứ tự
start V1/assistance, submit đồng thời, CLI chạy song song, hai data roots, child
crash, timeout và shutdown. Kiểm busy chỉ để hiển thị; lease quyết định thật.
API chỉ import helper stdlib; test fresh-process imports để phát hiện model
package bị load vào process API qua helper. Chạy
`../.venv/Scripts/python.exe -m pytest tests/integration/test_inference_lease.py -q`.

- [ ] **Step 8: Chạy worker GREEN và benchmark regression**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_worker.py -q`

Run: `../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation_benchmark -q`

Expected: PASS cả hai; model env vẫn không bị import khi API tests boot.

- [ ] **Step 9: Commit Task 3**

```powershell
git add backend/app/annotation/assistance_protocol.py backend/app/annotation/assistance_child.py backend/app/annotation/assistance_worker.py backend/app/annotation/settings.py backend/tests/annotation/test_assistance_protocol.py backend/tests/annotation/test_assistance_worker.py backend/app/inference_lease.py backend/app/owned_process.py backend/app/v1/worker.py backend/app/annotation_benchmark/runner.py backend/tests/integration/test_inference_lease.py
git commit -m "feat(v2): run assisted inference in an isolated worker"
```

---

### Task 4: Assistance API và app lifecycle

**Files:**
- Modify: `backend/app/annotation/api.py`
- Modify: `backend/app/v1/api.py`
- Modify: `scripts/generate-annotation-types.py`
- Test: `backend/tests/annotation/test_assistance_api.py`
- Test: `backend/tests/v1/test_launcher.py`
- Test: `backend/tests/v1/test_api.py`

**Interfaces:**
- Consumes: `AssistanceRepository`, `AssistanceWorker`, contracts Task 1.
- Produces: `/assist-models`, run create/list/get/cancel, suggestion list/reject routes.

`GET /clips/{clip_id}/assist-runs?active_only=true&limit=50&cursor=...` trả
`AssistanceRunListView`; bỏ `active_only` để xem history. Không biết run ID vẫn
khôi phục được mọi queued/running run sau reload hoặc đổi clip rồi quay lại.

- [ ] **Step 1: Viết RED HTTP contract tests**

```python
def test_start_run_returns_202_and_never_accepts_client_paths(client, ready_clip):
    response = client.post(
        f"/api/v2/annotations/clips/{ready_clip.id}/assist-runs",
        json={
            "operation_id": "00000000-0000-0000-0000-000000000031",
            "expected_clip_revision": ready_clip.revision,
            "model": "dino", "start_frame": 10, "end_frame": 80,
        },
    )
    assert response.status_code == 202
    assert "source_path" not in response.text
    assert "model_root" not in response.text

def test_reject_does_not_create_annotation_or_coverage(client, pending_suggestion):
    before = client.get(f"/api/v2/annotations/clips/{pending_suggestion.clip_id}/actions").json()
    response = client.post(
        f"/api/v2/annotations/clips/{pending_suggestion.clip_id}/assist-suggestions/{pending_suggestion.id}/reject",
        json={"operation_id": "00000000-0000-0000-0000-000000000032",
              "expected_clip_revision": pending_suggestion.clip_revision},
    )
    after = client.get(f"/api/v2/annotations/clips/{pending_suggestion.clip_id}/actions").json()
    assert response.status_code == 200
    assert after == before
```

Add 404 cross-clip, 409 stale/conflict, 422 bad range/model, 503 queue-full/
worker unavailable, unavailable model info, idempotent retry, cancel transitions,
pagination/filter state and no filesystem paths in any response.

- [ ] **Step 2: Chạy API RED**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_api.py -q`

Expected: FAIL 404 routes.

- [ ] **Step 3: Implement routes và stable error codes**

```python
@router.post("/clips/{clip_id}/assist-runs", response_model=AssistanceRunView, status_code=202)
def create_assist_run(clip_id: UUID, request: AssistanceRunCreate):
    result = assistance.create_run(clip_id, request)
    assistance_worker.wake()
    return result

@router.get("/clips/{clip_id}/assist-suggestions", response_model=AssistanceSuggestionListView)
def list_assist_suggestions(clip_id: UUID, state: SuggestionReviewState = "pending"):
    return assistance.list_suggestions(clip_id, state=state)
```

Map `SuggestionConflict`/stale→409, model unavailable→503, frame range→422.
Model availability dùng interpreter probe + asset manifest/hashes theo cơ chế
benchmark đã có; manifest hiện không có chữ ký số. Không thêm yêu cầu signed
manifest. Job đợi lease trả queued, không trả lỗi rỗng; probe không load weights
trong route và availability được cache có thời hạn.

- [ ] **Step 4: Nối lifecycle và viết startup/shutdown assertions**

`create_app` tạo một `AssistanceRepository` và `AssistanceWorker`, lưu trong
`application.state`, gọi `reconcile_startup/start/stop` cùng worker khác. Test
factory injection để API test không spawn model thật. Shutdown order: assistance
worker → preparation worker → tracking worker → annotation DB.
Lifecycle tests đặt trong `tests/v1/test_api.py`, launcher subprocess tests trong
`tests/v1/test_launcher.py`; không gộp kiểm lifecycle vào test đọc script.

- [ ] **Step 5: Chạy API/startup GREEN**

Run: `../.venv/Scripts/python.exe -m pytest tests/annotation/test_assistance_api.py tests/v1/test_api.py tests/v1/test_launcher.py -q`

Expected: PASS và không có child process sau TestClient shutdown.

- [ ] **Step 6: Regenerate và verify OpenAPI contract**

Thêm DTO mới vào imports và `MODELS` của generator hiện có; đây là generator từ
Pydantic schema, không tự quét OpenAPI routes. Test bằng `test_type_generation.py`.

Run từ root: `./.venv/Scripts/python.exe scripts/generate-annotation-types.py`

Run từ root: `./.venv/Scripts/python.exe scripts/generate-annotation-types.py --check`

Expected: generated JSON/TypeScript khớp backend; không hand-edit generated types.

- [ ] **Step 7: Commit Task 4**

```powershell
git add backend/app/annotation/api.py backend/app/v1/api.py backend/tests/annotation/test_assistance_api.py backend/tests/v1/test_api.py backend/tests/v1/test_launcher.py scripts/generate-annotation-types.py frontend/src/annotation/schema.generated.json frontend/src/annotation/types.generated.ts
git commit -m "feat(v2): expose model assistance jobs to the annotation API"
```

---

### Task 5: Panel Model hỗ trợ và proposal-to-draft flow

**Files:**
- Create: `frontend/src/annotation/AssistedReviewPanel.tsx`
- Create: `frontend/src/annotation/AssistedReviewPanel.test.tsx`
- Modify: `frontend/src/annotation/api.ts`
- Modify: `frontend/src/annotation/ActionWorkspace.tsx`
- Modify: `frontend/src/annotation/ActionWorkspace.test.tsx`
- Modify: `frontend/src/annotation/ActionEditor.tsx`
- Modify: `frontend/src/annotation/ActionEditor.test.tsx`
- Modify: `frontend/src/annotation/actionRules.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: generated `AssistanceModelInfo`, `AssistanceRunView`, `AssistanceSuggestionView`.
- Produces: `AssistedReviewPanel` callbacks `onSeek(frame)`, `onUseSuggestion(suggestion)`, `onQueueChanged()`.
- Produces: `ActionDraft.suggestion_id?: string | null` và `ActionDraft.estimated?: boolean` chỉ ở client; request gửi `suggestion_id`, không gửi `estimated`.

- [ ] **Step 1: Viết RED API-client tests**

```typescript
it("starts an assistance run without sending a filesystem path", async () => {
  await startAssistanceRun("clip", {
    operation_id: "op", expected_clip_revision: 4,
    model: "dino", start_frame: 10, end_frame: 80,
  });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v2/annotations/clips/clip/assist-runs",
    expect.objectContaining({ body: expect.not.stringContaining("path") }),
  );
});
```

- [ ] **Step 2: Chạy client RED**

Run: `pnpm test -- --run src/annotation/AssistedReviewPanel.test.tsx`

Expected: FAIL missing exports/component.

- [ ] **Step 3: Implement typed API calls**

Add `listAssistanceModels`, `startAssistanceRun`, `listAssistanceRuns`, `getAssistanceRun`,
`cancelAssistanceRun`, `listAssistanceSuggestions`, `rejectAssistanceSuggestion`.
Tất cả nhận AbortSignal; errors tiếp tục dùng `AnnotationApiError` stable code.

- [ ] **Step 4: Viết RED panel behavior tests**

```typescript
it("uses a suggestion as an estimated draft but does not save it", async () => {
  render(<AssistedReviewPanel {...fixtureProps} />);
  await user.click(screen.getByRole("button", { name: /Đoạn 120–145/ }));
  expect(fixtureProps.onSeek).toHaveBeenCalledWith(120);
  await user.click(screen.getByRole("button", { name: "Dùng làm nháp" }));
  expect(fixtureProps.onUseSuggestion).toHaveBeenCalledWith(
    expect.objectContaining({ id: "suggestion-1", crossing_estimate: 132 }),
  );
  expect(fixtureProps.onSave).toBeUndefined();
});

it("does not call an empty queue no-action", async () => {
  render(<AssistedReviewPanel {...fixtureProps} suggestions={[]} />);
  expect(screen.getByText(/model không tìm thấy gợi ý/i)).toBeVisible();
  expect(screen.getByText(/vẫn phải xem phần còn lại/i)).toBeVisible();
});
```

Add availability/default DINO, range I/O exact-frame capture, invalid range,
start and bounded polling, progress, failed/OOM, cancel, stale, reject, filters,
AbortController on clip switch and late response ignored.

Test unmount/remount khi run queued/running: gọi list active runs, tiếp tục poll
đúng run ID, hủy được, không enqueue lần nữa. Test history succeeded/failed/
cancelled sau restart và không gắn response của clip A vào clip B.

- [ ] **Step 5: Implement `AssistedReviewPanel` tối thiểu**

Panel state machine:

```typescript
type PanelState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "queued"; run: AssistanceRunView }
  | { kind: "running"; run: AssistanceRunView }
  | { kind: "failed"; run: AssistanceRunView }
  | { kind: "cancelled"; run: AssistanceRunView }
  | { kind: "ready"; run: AssistanceRunView; items: AssistanceSuggestionView[] };
```

Poll 1s→2s→4s, cap 4s; clear timer và abort fetch khi clip/run đổi. Không
optimistically mark accepted; reject chỉ đổi state sau response. Hiển thị label
nullable là “Chỉ cần xem”, reason tiếng Việt và badge “ước lượng”.
Khi mount/đổi clip: load active runs + history + suggestion queue, rồi poll active
run được chọn. Queue pending rỗng sau khi tất cả proposal đã xử lý không được
hiển thị như model tìm được zero proposal; phân biệt tổng run count và bộ lọc.

- [ ] **Step 6: Chạy panel GREEN**

Run: `pnpm test -- --run src/annotation/AssistedReviewPanel.test.tsx`

Expected: PASS.

- [ ] **Step 7: Viết RED workspace/editor integration tests**

```typescript
it("saves a reviewed proposal through the normal annotation endpoint", async () => {
  render(<ActionWorkspace {...props} />);
  await useSuggestion("suggestion-1");
  expect(screen.getByText(/mốc do model gợi ý/i)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Lưu nhãn" }));
  expect(mocks.createAction).toHaveBeenCalledWith(
    "clip",
    expect.objectContaining({ suggestion_id: "suggestion-1" }),
  );
  expect(mocks.confirmAction).not.toHaveBeenCalled();
  expect(mocks.createReviewCoverage).not.toHaveBeenCalled();
});
```

Add edit estimated frames/label, suggestion without label, manual cancel clears
provenance, 409 keeps draft, accepted item leaves pending queue only after save,
and manual create sends `suggestion_id: null`.

- [ ] **Step 8: Nối panel vào `ActionWorkspace` và editor**

Render panel trước `ActionEditor` chỉ ở mode Gán nhãn. `onUseSuggestion` seeks
view start, sets draft:

```typescript
{
  suggestion_id: item.id,
  estimated: true,
  label: item.label,
  start_frame: item.action_start_frame,
  end_frame: item.action_end_frame,
  crossing_frame: item.crossing_estimate,
  interaction_id: null,
  object_kind: "unknown",
  visibility: "clear",
  uncertain_labels: [],
  unclear_reason: null,
}
```

Nếu label null, UI không được ngầm lưu `hand_in`: draft mở label chưa chọn bằng
cách mở rộng client-only label thành `ActionLabel | null`; nút save disabled đến
khi user chọn. `ActionEditor` banner nêu provenance và estimated, nhưng submit
chỉ serialize fields backend nhận. Save success refresh suggestion queue.

`validateActionDraft` kiểm `label === null` trước mọi label-specific logic;
manual `emptyDraft()` vẫn giữ default hiện có. Lượt tay phải được chọn/tạo rõ
ràng; proposal track ID không tự trở thành interaction. Block submit qua cả
button, Enter và Ctrl+S khi label/lượt tay/frame chưa đủ; test từng đường submit.
Test proposal `label=null` thật từ DINO mở editor vẫn chưa chọn nhãn. Payload
serialize allowlist explicit, không spread `estimated` vào body vì backend Dto
đang `extra='forbid'`. Khi mở suggestion khác, dùng dirty-draft guard hiện có.

- [ ] **Step 9: Chạy frontend focused GREEN**

Run: `pnpm test -- --run src/annotation/AssistedReviewPanel.test.tsx src/annotation/ActionWorkspace.test.tsx src/annotation/ActionEditor.test.tsx`

Expected: PASS.

- [ ] **Step 10: Commit Task 5**

```powershell
git add frontend/src/annotation/AssistedReviewPanel.tsx frontend/src/annotation/AssistedReviewPanel.test.tsx frontend/src/annotation/api.ts frontend/src/annotation/ActionWorkspace.tsx frontend/src/annotation/ActionWorkspace.test.tsx frontend/src/annotation/ActionEditor.tsx frontend/src/annotation/ActionEditor.test.tsx frontend/src/annotation/actionRules.ts frontend/src/styles.css
git commit -m "feat(v2): review model proposals in the labeling workspace"
```

---

### Task 6: End-to-end truthfulness, runbook và regression gate

**Files:**
- Modify: `docs/v2-user-guide.md`
- Modify: `docs/v2-assisted-benchmark-runbook.md`
- Modify: `docs/v2-acceptance.md`
- Create: `backend/tests/integration/test_assisted_review_flow.py`
- Modify: `scripts/start-v1.ps1`
- Test: `backend/tests/v1/test_launcher.py`

**Interfaces:**
- Consumes: full API/UI/worker flow Tasks 1–5 and installed benchmark assets.
- Produces: launcher preflight that reports model availability without blocking manual mode; acceptance evidence and operator instructions.

- [ ] **Step 1: Viết RED integration test cho fake child full flow**

```python
def test_assisted_proposal_requires_human_save_and_separate_confirmation(app_fixture):
    run = app_fixture.start_assistance(model="dino", frames=(10, 80))
    app_fixture.complete_child(run, proposal=crossing_proposal(30, 44, 37))
    suggestion = app_fixture.wait_suggestions(run)[0]
    assert app_fixture.actions() == []
    action = app_fixture.save_action_from_suggestion(suggestion, exact=(29, 45, 38))
    assert action.review_state == "draft"
    assert app_fixture.suggestion(suggestion.id).review_state == "accepted"
    assert app_fixture.coverage() == []
    assert app_fixture.confirm_action(action.id).review_state == "confirmed"
```

Add restart persistence, ROI change while child runs, unavailable model/manual
create, empty proposal wording contract and launcher no-model behavior.

- [ ] **Step 2: Chạy integration RED**

Run: `../.venv/Scripts/python.exe -m pytest tests/integration/test_assisted_review_flow.py -q`

Expected: FAIL cho đến khi complete wiring từ Tasks 1–5 hoạt động trong fixture.

- [ ] **Step 3: Hoàn thiện wiring tối thiểu để integration GREEN**

Không thêm feature ngoài spec. Sửa đúng boundary gây failure; mọi bug phát hiện
phải có regression test fail trước. Launcher chỉ log:

```text
[V1] Assisted labeling: available (dino, mediapipe)
```

hoặc:

```text
[V1] Assisted labeling: unavailable; manual labeling remains available
```

Thiếu model không làm launcher exit non-zero.

- [ ] **Step 4: Chạy integration GREEN**

Run: `../.venv/Scripts/python.exe -m pytest tests/integration/test_assisted_review_flow.py tests/v1/test_launcher.py -q`

Expected: PASS.

- [ ] **Step 5: Cập nhật tài liệu operator/acceptance**

`v2-user-guide.md` mô tả chọn range, model, queue, “ước lượng”, use/reject và
bắt buộc review ngoài proposal. Runbook phân biệt benchmark CLI với UI assisted
review. Ledger có SOFTWARE/UI/MODEL_SMOKE riêng; QUALITY/EFFORT vẫn
`PENDING_DATA`, ghi MediaPipe sparse và DINO noisy từ run hiện có.

- [ ] **Step 6: Chạy full automated verification**

```powershell
# backend/, app environment
../.venv/Scripts/python.exe -m pytest tests --ignore=tests/annotation_benchmark -q
../.venv/Scripts/python.exe -m compileall -q app

# backend/, isolated model environment
../.venv-assist-benchmark/Scripts/python.exe -m pytest tests/annotation_benchmark tests/annotation/test_assistance_protocol.py -q

# frontend/
pnpm test -- --run
pnpm exec tsc --noEmit
pnpm build

# repository root
git diff --check
git status --short
```

Expected: tất cả exit 0; chỉ các warning dependency đã ghi nhận được phép còn.

- [ ] **Step 7: Chạy real Windows smoke trên đoạn ngắn đã freeze**

Thực hiện quy trình data-root/launcher bên dưới trước. Start app từ feature
worktree, chọn clip/ROI đã xác minh binding và chạy DINO
trên tối đa 300 frames. Ghi run ID, processed/scheduled, proposal count,
model/device/asset hashes, end-to-end time. Dùng một proposal tạo draft với
exact frames đã xem; không confirm nếu đó không phải nhãn thật mà người dùng đã
duyệt. Nếu không có proposal, ghi model smoke success + quality limitation,
không tạo nhãn giả. Test MediaPipe availability/load riêng; không cần chạy cả
clip nếu DINO đã chứng minh UI flow.

- [ ] **Step 8: Kiểm dữ liệu và process sau smoke**

Verify source SHA không đổi, không PNG/mask/video copy mới dưới assistance
artifact, accepted suggestion link đúng draft, không tạo coverage mới (coverage
chồng action có thể bị invalidate đúng logic hiện có), app restart
đọc lại run history, và không còn orphan model child sau stop.

#### Data root, launcher và rollback khi thử bản mới (bắt buộc trước Step 7)

1. Đọc `launcher-state.json` của instance đang chạy để lấy đúng project root,
   launcher directory, source data root, annotation root, ports và owned PIDs.
   Không đoán root theo worktree hoặc dựa tên clip. Ghi các giá trị đã xác minh
   vào private handoff manifest, không commit paths/video/nhãn vào Git.
2. Chạy automated migration/browser fixture trên dữ liệu synthetic riêng trước;
   data binding của fixture phải được tạo bằng API/repository bình thường, không
   sửa binding DB của người dùng. Không nhân bản video shop để dựng môi trường.
3. Với source root thật, chỉ chuyển lúc V1/preparation/assistance idle và bản nháp
   UI đã được lưu. Dùng launcher `-Stop` của đúng instance cùng LauncherDirectory
   đã resolve để giải phóng owner lock; không kill theo tên và không chạy hai
   API sở hữu một annotation root dù ports khác nhau.
4. Khi DB idle, tạo snapshot backup mới bằng SQLite backup API vào tên duy nhất
   có timestamp/UUID; lưu hash, schema version và binding. Không dùng lại file
   `backup-v2` cũ nếu tồn tại. Giữ pre-upgrade backup và toàn bộ DB/WAL/SHM hiện
   hành khi cần điều tra; không copy riêng DB đang có WAL để làm backup.
5. Feature launcher nhận cùng `-DataDirectory` đã resolve; env
   `V2_ANNOTATION_ROOT` giữ đúng override đã xác minh. `-LauncherDirectory` dùng
   directory riêng cho instance feature. Cấu hình explicit
   `V2_ANNOTATION_ASSISTANCE_PYTHON` và `V2_ANNOTATION_ASSISTANCE_MODEL_ROOT`
   bằng interpreter/asset path đã kiểm, tránh default root khác mất clip/model.
   Kiểm launcher args theo script thực tế, không invent command flags.
6. Start feature launcher, verify health/schema 3/source-root fingerprint,
   model availability và clip ID/ROI cũ. Mở URL thực trả từ launcher, test thấy
   panel Model hỗ trợ và chạy một đoạn. Khởi động đúng feature phải là phần
   bàn giao; merge nhánh không phải điều kiện để thử UI.
7. Nếu startup/migration thất bại trước user writes: dừng đúng feature instance,
   giữ lại failed DB artifacts, phục hồi snapshot qua thủ tục có kiểm hash/binding
   và lock, rồi start launcher cũ bằng manifest. Migration rollback bằng transaction
   trước; không tự phục hồi snapshot cho mọi exception.
8. Nếu đã phát sinh nhãn/gợi ý mới trên v3: không restore v2 vì sẽ mất dữ liệu
   mới. Ưu tiên sửa tiến trên v3; muốn quay v2 phải có quyết định riêng về bảo toàn
   dữ liệu. Bản code cũ hỗ trợ schema 2 sẽ từ chối v3; ghi rõ trong runbook.

Acceptance launcher cần test source-root mismatch, owner conflict, missing
model config, migration failure và reload đúng clip. Chỉ xóa/move DB khi bước
phục hồi thật đã xác minh target và có thẩm quyền phù hợp; plan không thực hiện
thao tác đó trong lượt chỉnh tài liệu.

- [ ] **Step 9: Self-review diff theo spec rồi commit**

```powershell
git add docs/v2-user-guide.md docs/v2-assisted-benchmark-runbook.md docs/v2-acceptance.md backend/tests/integration/test_assisted_review_flow.py scripts/start-v1.ps1 backend/tests/v1/test_launcher.py
git commit -m "docs(v2): verify the assisted review workflow"
```

---

## Plan self-review

### Spec coverage

| Spec requirement | Task |
| --- | --- |
| Model dependency/process isolation | 3, 4 |
| DINO default, MediaPipe optional, closed registry | 1, 3, 5 |
| Run/proposal persistence and audit | 1 |
| Atomic proposal acceptance through normal annotation | 2 |
| Queue/cancel/recovery/deadline/tracking-busy | 3, 4 |
| HTTP contracts without client paths | 4 |
| UI range/progress/queue/seek/use/reject | 5 |
| Estimate truthfulness and empty-result wording | 5, 6 |
| No automatic annotation/confirmation/coverage | 2, 5, 6 |
| Stale source/ROI/guideline and concurrency | 1–4 |
| Quota/private artifacts/no media copies | 3, 6 |
| Manual mode survives missing model | 3–6 |
| Restart/real model smoke/full regression | 6 |
| QUALITY/EFFORT remain pending | 6 |

### Placeholder and type consistency check

- Đã đối chiếu script generator, base DTO, inheritance Create/Update và test
  paths với repo. Ví dụ test có helper mới phải khai báo trong test utilities;
  mỗi task vẫn phải chạy RED/GREEN thật khi implement.
- `AssistanceRunCreate`, `AssistanceRunView`, `AssistanceSuggestionView` dùng
  thống nhất từ store → API → generated TypeScript → panel.
- `suggestion_id` chỉ thêm vào create action, không thêm vào update action; sau
  khi annotation đã tạo, provenance nằm ở suggestion link bất biến.
- Client-only `estimated` không truyền qua API; frontend serialize allowlist,
  backend Dto extra-forbid từ chối field ngoài contract.
- Task 6 là gate tích hợp của cùng feature, không mở thêm subsystem training/M3.

### Audit closure (2026-09-12)

| Finding | Thay đổi bắt buộc | Bằng chứng cần thu khi implement |
| --- | --- | --- |
| 1. FK claim trước INSERT | Task 2 INSERT rồi claim cùng transaction | FK bật, rollback sau INSERT, concurrent accept |
| 2. Model setting bắt buộc làm hỏng boot | Task 3 optional paths, availability | Boot/manual save khi không có config hoặc asset |
| 3. Schema v3 làm hỏng benchmark | Task 1 reader v2/v3 allowlist | freeze/run/evaluate v3, future schema rejected |
| 4. Race GPU | Task 3 lease chung, sửa cả V1 và CLI | Hai thứ tự start, concurrent processes/data roots |
| 5. Reload mất run | Tasks 1/4 list runs; Task 5 restore polling | Remount, cancel resumed run, history |
| 6. Ép null thành hand_in | Task 5 giữ null, validate submit | Button/Enter/Ctrl+S đều chặn chưa chọn label |
| 7. Contract/lệnh sai | Tasks 1/2/4 DTO, generator MODELS, script thật | Type-generation test, Update rejects provenance |
| 8. Data-root/launcher handoff thiếu | Task 6 quy trình có backup/binding/lock | Bản feature mở đúng clip/ROI và recovery test |

Closure ở đây nghĩa là plan đã có bước xử lý finding; chưa khẳng định các bước
đã được implement hoặc nghiệm thu.
