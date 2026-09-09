# V1 Offline Person Tracking Implementation Plan

> SUPERSEDED after the Astra audit. Do not execute the tasks below. The active plan is [2026-09-09-v1-offline-person-tracking-revised.md](2026-09-09-v1-offline-person-tracking-revised.md). This file preserves the original plan for comparison; its self-review claims were not supported by the audit.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Build a local MP4-import workflow that outputs a browser-playable YOLO26/ByteTrack person-tracking video with local numeric IDs.

**Architecture:** A new \`app.v1\` boundary owns imported files, job state, sequential tracking, and safe result serving. The current RTSP/live/review runtime is not started or exposed from the V1 API. React becomes a single upload-and-result screen which only calls \`/api/v1\`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite, OpenCV, FFmpeg, Ultralytics YOLO26, ByteTrack, CUDA/PyTorch, React, TypeScript, Vite, pytest, Vitest.

**Spec:** \`docs/superpowers/specs/2026-09-09-v1-offline-person-tracking-design.md\`

## Global Constraints

- Accept one local MP4 at a time; preserve the source MP4 unchanged in an application-owned local job directory.
- Never start RTSP, HLS, WebRTC, MediaMTX, pose, hand, cash, goods, zones, seller/customer, action, transaction, review, or training code from V1.
- Use local \`yolo26n.pt\`, COCO class \`person\` only, and one \`persist=True\` ByteTrack state per job.
- Decode every frame in source order; do not drop frames or batch unrelated frames.
- Render boxes on original-resolution frames and finalise output as H.264/yuv420p.
- Never expose video paths, model paths, credentials, internal exceptions, or FFmpeg commands in responses/UI.
- Local IDs are video-scoped and not identities; a re-entry after full exit may get a new ID.
- Show measured processing metrics, not promised FPS/completion time.
- Include the Ultralytics AGPL-3.0/Enterprise license notice.

---

## File Structure

| File | Responsibility |
| --- | --- |
| \`backend/app/v1/contracts.py\` | Job status, safe response models, public failure codes. |
| \`backend/app/v1/storage.py\` | MP4 import, metadata probe, private job paths. |
| \`backend/app/v1/tracker.py\` | YOLO26 person-only frame inference and drawing. |
| \`backend/app/v1/worker.py\` | One queued sequential worker, metrics, H.264 finalisation. |
| \`backend/app/models.py\` | Persistent \`VideoTrackingJob\`. |
| \`backend/app/api.py\` | V1-only import/start/status/source/result/health endpoints. |
| \`frontend/src/VideoImport.tsx\` | MP4 chooser/drop and metadata confirmation. |
| \`frontend/src/TrackingResult.tsx\` | Progress, player, metrics, download link. |
| \`frontend/src/App.tsx\` | V1 screen composition; no camera/review components. |
| \`docs/v1-user-guide.md\` | SmartPSS export and local V1 user instructions. |

## Task 1: Isolate V1 runtime and persistent job contract

**Files:**

- Create: \`backend/app/v1/__init__.py\`, \`backend/app/v1/contracts.py\`, \`backend/tests/unit/test_v1_contracts.py\`
- Modify: \`backend/app/config.py\`, \`backend/app/models.py\`, \`backend/app/api.py\`, \`backend/pyproject.toml\`

**Interfaces:**

- \`VideoJobStatus = Literal["imported", "queued", "processing", "ready", "failed"]\`.
- \`VideoTrackingJob\` contains \`id\`, \`original_name\`, \`source_file\`, \`result_file\`, \`status\`, \`frame_count\`, \`processed_frames\`, \`width\`, \`height\`, \`duration_ms\`, \`inference_ms_total\`, \`device\`, and \`failure_code\`.
- Settings add \`tracking_data_dir: Path\`, \`tracking_model_path: Path\`, \`tracking_device: str\`, \`tracking_image_size: int\`.
- Lifespan only creates the database; it never instantiates \`LiveStreamManager\` or \`PersonInferenceWorker\`.

- [ ] **Step 1: Write failing tests.**

~~~python
from app.v1.contracts import VideoJobStatus, safe_failure_code

def test_v1_statuses_only_cover_import_work_and_result():
    assert {item.value for item in VideoJobStatus} == {
        "imported", "queued", "processing", "ready", "failed",
    }

def test_safe_failure_code_hides_internal_path():
    assert safe_failure_code("OSError: C:/private/yolo26n.pt") == "khong_the_xu_ly_video"
~~~

~~~python
def test_api_module_does_not_import_live_runtime():
    source = Path("app/api.py").read_text(encoding="utf-8")
    assert "LiveStreamManager" not in source
    assert "PersonInferenceWorker" not in source
~~~

- [ ] **Step 2: Run the test and verify failure.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_contracts.py -v\`

Expected: FAIL with \`ModuleNotFoundError: No module named 'app.v1'\`.

- [ ] **Step 3: Implement the contract, settings, model, and V1-only lifespan.**

~~~python
# backend/app/v1/contracts.py
from enum import StrEnum

class VideoJobStatus(StrEnum):
    IMPORTED = "imported"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

def safe_failure_code(_internal_error: str) -> str:
    return "khong_the_xu_ly_video"
~~~

~~~python
# backend/app/config.py additions
PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    tracking_data_dir: Path = PROJECT_ROOT / "data" / "v1-tracking"
    tracking_model_path: Path = PROJECT_ROOT / "yolo26n.pt"
    tracking_device: str = "0"
    tracking_image_size: int = 960
~~~

~~~python
# backend/app/models.py addition
class VideoTrackingJob(Base):
    __tablename__ = "video_tracking_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file: Mapped[str] = mapped_column(String, nullable=False)
    result_file: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    processed_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    inference_ms_total: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    device: Mapped[str | None] = mapped_column(String(32))
    failure_code: Mapped[str | None] = mapped_column(String(64))
~~~

Remove the live manager, live routes, inference routes, review-case routes, and their lifespan startup/shutdown calls from \`app/api.py\`. Add \`python-multipart>=0.0.9,<1\` to dependencies.

- [ ] **Step 4: Run focused contract/database tests.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_contracts.py tests/integration/test_db.py -v\`

Expected: PASS.

- [ ] **Step 5: Commit.**

~~~powershell
git add backend/app/v1/__init__.py backend/app/v1/contracts.py backend/app/config.py backend/app/models.py backend/app/api.py backend/pyproject.toml backend/tests/unit/test_v1_contracts.py
git commit -m "feat: isolate v1 video tracking runtime"
~~~

## Task 2: Import MP4 and return truthful metadata

**Files:**

- Create: \`backend/app/v1/storage.py\`, \`backend/tests/unit/test_v1_storage.py\`, \`backend/tests/integration/test_v1_api.py\`
- Modify: \`backend/app/api.py\`

**Interfaces:**

- \`VideoMetadata(frame_count: int, width: int, height: int, duration_ms: int)\`.
- \`VideoStore.import_mp4(upload: UploadFile, job_id: str) -> tuple[Path, VideoMetadata]\`.
- \`POST /api/v1/jobs\` accepts multipart field \`video\`; \`GET /api/v1/jobs/{job_id}\` returns a safe job payload.

- [ ] **Step 1: Write failing storage and import tests.**

~~~python
def test_import_rejects_non_mp4_before_creating_job_directory(tmp_path):
    store = VideoStore(tmp_path, probe=lambda _: VideoMetadata(25, 1920, 1080, 1000))
    with pytest.raises(UnsupportedVideoError):
        store.import_mp4(UploadFile(filename="camera.avi", file=BytesIO(b"x")), "job-1")
    assert not (tmp_path / "job-1").exists()

def test_import_copies_mp4_and_returns_metadata(tmp_path):
    store = VideoStore(tmp_path, probe=lambda _: VideoMetadata(25, 1920, 1080, 1000))
    source, metadata = store.import_mp4(
        UploadFile(filename="shop.mp4", file=BytesIO(b"video")), "job-1"
    )
    assert source.read_bytes() == b"video"
    assert metadata == VideoMetadata(25, 1920, 1080, 1000)
~~~

~~~python
def test_create_job_hides_server_file_path(client, valid_mp4_upload):
    response = client.post("/api/v1/jobs", files={"video": valid_mp4_upload})
    payload = response.json()
    assert response.status_code == 201
    assert payload["status"] == "imported"
    assert payload["original_name"] == "shop.mp4"
    assert "source_file" not in payload
~~~

- [ ] **Step 2: Run focused tests and verify failure.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_storage.py tests/integration/test_v1_api.py -v\`

Expected: FAIL because \`VideoStore\` and V1 routes do not exist.

- [ ] **Step 3: Implement bounded local import.**

~~~python
class VideoStore:
    def import_mp4(self, upload: UploadFile, job_id: str) -> tuple[Path, VideoMetadata]:
        if Path(upload.filename or "").suffix.lower() != ".mp4":
            raise UnsupportedVideoError()
        job_dir = self._root / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        destination = job_dir / "source.mp4"
        with destination.open("wb") as target:
            while chunk := upload.file.read(1024 * 1024):
                target.write(chunk)
        metadata = self._probe(destination)
        if min(metadata.frame_count, metadata.width, metadata.height) <= 0:
            raise UnsupportedVideoError()
        return destination, metadata
~~~

Use OpenCV only inside the \`probe\` implementation. Map unreadable videos to public code \`khong_the_doc_video\`. Remove the job directory only when import fails before the database row exists. Persist the path internally but return the safe fields in the test.

- [ ] **Step 4: Run focused tests.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_storage.py tests/integration/test_v1_api.py -v\`

Expected: PASS.

- [ ] **Step 5: Commit.**

~~~powershell
git add backend/app/v1/storage.py backend/app/api.py backend/tests/unit/test_v1_storage.py backend/tests/integration/test_v1_api.py
git commit -m "feat: import local MP4 tracking jobs"
~~~

## Task 3: Implement YOLO26 person-only ByteTrack adapter

**Files:**

- Create: \`backend/app/v1/tracker.py\`, \`backend/tests/unit/test_v1_tracker.py\`
- Modify: \`backend/app/vision/bytetrack_retail.yaml\`

**Interfaces:**

- \`TrackedPerson(track_id: int, confidence: float, box: tuple[float, float, float, float])\`.
- \`PersonTracker(model_path: Path, device: str, image_size: int, tracker_config: Path)\`.
- \`track_frame(frame: numpy.ndarray) -> tuple[list[TrackedPerson], float]\`.
- \`annotate_people(frame, people) -> numpy.ndarray\`.

- [ ] **Step 1: Write failing tracker tests.**

~~~python
def test_tracker_uses_person_class_persistent_bytetrack(fake_yolo, frame):
    tracker = PersonTracker.from_model(fake_yolo, "0", 960, Path("bytetrack.yaml"))
    people, _ = tracker.track_frame(frame)
    assert fake_yolo.calls[0][1] == {
        "classes": [0], "persist": True, "tracker": "bytetrack.yaml",
        "imgsz": 960, "device": "0", "verbose": False,
    }
    assert [person.track_id for person in people] == [7]

def test_annotation_writes_only_person_id_and_confidence(frame):
    output = annotate_people(frame, [TrackedPerson(7, 0.86, (10, 20, 30, 40))])
    assert output.shape == frame.shape
    assert output[20:41, 10:31].any()
~~~

- [ ] **Step 2: Run focused test and verify failure.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_tracker.py -v\`

Expected: FAIL with \`ModuleNotFoundError: No module named 'app.v1.tracker'\`.

- [ ] **Step 3: Implement one local YOLO model and annotation.**

~~~python
def track_frame(self, frame: np.ndarray) -> tuple[list[TrackedPerson], float]:
    started = perf_counter()
    result = self._model.track(
        frame, classes=[0], persist=True, tracker=str(self._tracker_config),
        imgsz=self._image_size, device=self._device, verbose=False,
    )[0]
    elapsed_ms = (perf_counter() - started) * 1000
    if result.boxes is None or result.boxes.id is None:
        return [], elapsed_ms
    return self._people(result), elapsed_ms
~~~

Discard invalid boxes and detections without a tracker ID. Draw original-pixel boxes labelled only \`người #<id> · <confidence>%\`. Keep \`tracker_type: bytetrack\`; document each changed threshold and do not enable ReID.

- [ ] **Step 4: Run focused and existing tracker tests.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_tracker.py tests/unit/test_tracker.py -v\`

Expected: PASS.

- [ ] **Step 5: Commit.**

~~~powershell
git add backend/app/v1/tracker.py backend/app/vision/bytetrack_retail.yaml backend/tests/unit/test_v1_tracker.py
git commit -m "feat: add yolo26 person-only tracker"
~~~

## Task 4: Process one job sequentially and serve H.264 result

**Files:**

- Create: \`backend/app/v1/worker.py\`, \`backend/tests/unit/test_v1_worker.py\`
- Modify: \`backend/app/api.py\`, \`backend/app/models.py\`, \`backend/tests/integration/test_v1_api.py\`

**Interfaces:**

- \`TrackingWorker.submit(job_id: str) -> None\`, \`start() -> None\`, \`process(job_id: str) -> None\`.
- \`POST /api/v1/jobs/{job_id}/start\` transitions \`imported → queued\`.
- \`GET /api/v1/jobs/{job_id}/source\` and \`/result\` serve files only beneath \`tracking_data_dir\`.

- [ ] **Step 1: Write failing worker/result tests.**

~~~python
def test_worker_tracks_frames_in_order_and_records_measured_metrics(session, fake_decoder, fake_tracker, fake_encoder):
    job = imported_job(session)
    TrackingWorker(session_factory, fake_decoder, fake_tracker, fake_encoder).process(job.id)
    stored = session.get(VideoTrackingJob, job.id)
    assert fake_tracker.seen_frames == ["f0", "f1", "f2"]
    assert (stored.status, stored.processed_frames, stored.inference_ms_total) == ("ready", 3, 12.0)

def test_worker_hides_exception_and_keeps_original_file(session, fake_decoder, fake_tracker, fake_encoder):
    fake_decoder.raise_on_read = RuntimeError("C:/private/source.mp4")
    job = imported_job(session)
    TrackingWorker(session_factory, fake_decoder, fake_tracker, fake_encoder).process(job.id)
    stored = session.get(VideoTrackingJob, job.id)
    assert (stored.status, stored.failure_code, Path(stored.source_file).is_file()) == (
        "failed", "khong_the_xu_ly_video", True,
    )
~~~

~~~python
def test_ready_result_is_inline_mp4(client, ready_job):
    response = client.get("/api/v1/jobs/" + ready_job.id + "/result")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    assert "attachment" not in response.headers.get("content-disposition", "")
~~~

- [ ] **Step 2: Run tests and verify failure.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_worker.py tests/integration/test_v1_api.py -v\`

Expected: FAIL because worker/start/result endpoints do not exist.

- [ ] **Step 3: Implement queue, worker, and safe serving.**

~~~python
def process(self, job_id: str) -> None:
    with self._sessions() as session:
        job = session.get(VideoTrackingJob, job_id)
        if not job or job.status != VideoJobStatus.QUEUED:
            return
        job.status = VideoJobStatus.PROCESSING
        session.commit()
    try:
        self._decode_track_and_write(job_id)
    except Exception:
        self._mark_failed(job_id, "khong_the_xu_ly_video")
~~~

\`_decode_track_and_write\` opens \`source.mp4\`, writes an original-resolution intermediate overlay, calls \`track_frame\` once per decoded frame in source order, and saves \`processed_frames\` plus inference totals every 30 frames. It finalises \`annotated.mp4\` through FFmpeg using \`libx264\`, \`yuv420p\`, \`+faststart\`, and no audio. Mark a job \`ready\` only when FFmpeg succeeded and the result exists. Store \`cuda:0\` only if \`torch.cuda.is_available()\`, otherwise \`cpu\`.

Before every \`FileResponse\`, resolve the candidate and require that it is a regular file below \`tracking_data_dir\`; missing/unsafe files return a neutral 404.

- [ ] **Step 4: Run focused tests.**

Run: \`cd backend && python -m pytest tests/unit/test_v1_worker.py tests/integration/test_v1_api.py -v\`

Expected: PASS.

- [ ] **Step 5: Commit.**

~~~powershell
git add backend/app/v1/worker.py backend/app/api.py backend/app/models.py backend/tests/unit/test_v1_worker.py backend/tests/integration/test_v1_api.py
git commit -m "feat: render tracked person video jobs"
~~~

## Task 5: Replace the web with focused upload, progress, and result UI

**Files:**

- Create: \`frontend/src/VideoImport.tsx\`, \`frontend/src/TrackingResult.tsx\`
- Modify: \`frontend/src/App.tsx\`, \`frontend/src/api.ts\`, \`frontend/src/styles.css\`, \`frontend/src/App.test.tsx\`

**Interfaces:**

- \`createTrackingJob(file: File): Promise<TrackingJob>\`.
- \`startTrackingJob(id: string): Promise<TrackingJob>\`.
- \`getTrackingJob(id: string): Promise<TrackingJob>\`.
- \`VideoImport({ onImported })\`; \`TrackingResult({ job, onUpdated })\`.

- [ ] **Step 1: Write failing V1 UI tests.**

~~~tsx
it("imports MP4 and shows metadata before tracking starts", async () => {
  render(<App />);
  await userEvent.upload(
    screen.getByLabelText("Chọn video MP4"),
    new File(["video"], "shop.mp4", { type: "video/mp4" }),
  );
  expect(await screen.findByText("1920 × 1080 · 00:01")).toBeVisible();
  expect(screen.getByRole("button", { name: "Bắt đầu theo dõi người" })).toBeEnabled();
});

it("shows only tracking metrics and annotated video when ready", async () => {
  render(<App />);
  expect(await screen.findByText("GPU: cuda:0")).toBeVisible();
  expect(screen.getByTestId("annotated-video")).toHaveAttribute("src", "/api/v1/jobs/job-1/result");
  expect(screen.queryByText(/tiền|tay|người bán|giao dịch/i)).not.toBeInTheDocument();
});
~~~

- [ ] **Step 2: Run test and verify failure.**

Run: \`cd frontend && pnpm test -- --run src/App.test.tsx\`

Expected: FAIL because the current page fetches live/review APIs.

- [ ] **Step 3: Implement V1 page.**

~~~tsx
export default function App() {
  const [job, setJob] = useState<TrackingJob>();
  return <main>
    <header>
      <h1>Theo dõi người trong video</h1>
      <p>Chọn MP4 đã xuất từ đầu ghi. Video và AI chạy trong máy này.</p>
    </header>
    <VideoImport onImported={setJob} />
    {job && <TrackingResult job={job} onUpdated={setJob} />}
  </main>;
}
~~~

Render only: \`Chọn video MP4\`, \`Bắt đầu theo dõi người\`, \`đang xử lý\`, \`đã hoàn tất\`, \`không thể xử lý video\`, \`GPU\`, \`FPS xử lý\`, and \`thời gian AI trung bình\`. Show both original and annotated players after ready. Do not import \`LiveCamera\`, \`CaseList\`, \`CaseDetail\`, HLS, or review APIs.

- [ ] **Step 4: Run tests and production build.**

Run: \`cd frontend && pnpm test -- --run && pnpm build\`

Expected: PASS and Vite build completes.

- [ ] **Step 5: Commit.**

~~~powershell
git add frontend/src/App.tsx frontend/src/api.ts frontend/src/VideoImport.tsx frontend/src/TrackingResult.tsx frontend/src/styles.css frontend/src/App.test.tsx
git commit -m "feat: add v1 local video tracking screen"
~~~

## Task 6: Local launcher, operator guide, and RTX 3060 Ti acceptance run

**Files:**

- Create: \`docs/v1-user-guide.md\`, \`backend/tests/integration/test_v1_startup.py\`
- Modify: \`start-local.bat\`, \`README.md\`, \`.gitignore\`

**Interfaces:**

- \`GET /api/v1/health\` returns \`{"state": "ready", "device": "cuda:0"|"cpu"}\`.
- \`start-local.bat\` starts only V1 API and UI and contains no RTSP/registry reference.

- [ ] **Step 1: Write failing health/startup tests.**

~~~python
def test_health_returns_only_safe_execution_mode(client, monkeypatch):
    monkeypatch.setattr("app.v1.worker.cuda_available", lambda: True)
    assert client.get("/api/v1/health").json() == {"state": "ready", "device": "cuda:0"}

def test_start_script_has_no_rtsp_or_registry_reference():
    script = Path("../start-local.bat").read_text(encoding="utf-8")
    assert "RTSP" not in script
    assert "TRANSACTION_RTSP" not in script
~~~

- [ ] **Step 2: Run test and verify failure.**

Run: \`cd backend && python -m pytest tests/integration/test_v1_startup.py -v\`

Expected: FAIL because V1 health and V1-only startup do not exist.

- [ ] **Step 3: Implement health, V1-only launch, and guide.**

~~~bat
@echo off
setlocal
set "ROOT=%~dp0"
start "V1 Tracking API" /D "%ROOT%backend" cmd /k "python -m uvicorn app.api:app --host 127.0.0.1 --port 8000"
start "V1 Tracking UI" /D "%ROOT%frontend" cmd /k "pnpm dev --host 127.0.0.1"
start "" "http://127.0.0.1:5173"
endlocal
~~~

Write \`docs/v1-user-guide.md\` with the exact operator path: export a short MP4 clip in SmartPSS, double-click \`start-local.bat\`, choose the MP4, start tracking, then replay/download the result. State that the source stays local, output is not a transaction decision, IDs are local to one clip, and YOLO26 use requires an AGPL-3.0/Enterprise licensing choice.

- [ ] **Step 4: Run full automated verification.**

~~~powershell
cd backend; python -m pytest -q
cd ..\frontend; pnpm test -- --run; pnpm build
~~~

Expected: all backend tests pass, all frontend tests pass, and the production build succeeds.

- [ ] **Step 5: Perform and record one manual GPU acceptance run.**

1. Start V1 on the RTX 3060 Ti machine.
2. Import a short exported 1080p MP4.
3. Confirm health says \`cuda:0\`, then start processing.
4. Confirm the browser plays an H.264 result containing only \`người #ID\`.
5. Record source duration, dimensions, total processing seconds, effective FPS, mean inference milliseconds, and observed ID switches under **Máy đo thực tế** in \`docs/v1-user-guide.md\`. Do not record paths, camera URLs, or personal identity.

- [ ] **Step 6: Commit.**

~~~powershell
git add start-local.bat README.md .gitignore docs/v1-user-guide.md backend/tests/integration/test_v1_startup.py
git commit -m "docs: add v1 local tracking launch guide"
~~~

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 isolate MP4-only local import and safe metadata; Task 3 is YOLO26n/ByteTrack person-only inference; Task 4 creates sequential original-resolution H.264 results and measured metrics; Task 5 removes live/review UI; Task 6 removes RTSP startup, documents SmartPSS export/AGPL, and validates RTX 3060 Ti.
- **Placeholder scan:** No unresolved placeholder, vague test step, or undefined interface remains.
- **Type consistency:** \`VideoTrackingJob\`, \`VideoJobStatus\`, \`VideoStore\`, \`PersonTracker\`, and \`TrackingWorker\` are defined before use by API or UI.
