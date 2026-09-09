# V1 Offline Person Tracking — Revised Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development, selected by the user. Execute one task at a time with independent spec/quality review. A failed acceptance gate blocks dependent tasks.

**Goal:** Produce a real, playable YOLO26 person-tracking video first; then expose that proven processing service through a local MP4 web workflow.

**Architecture:** One sequential processing service is invoked first through a CLI, later by a single persistent-job worker. V1 has its own API entry point and database. Existing legacy API, tests and tracker configuration remain independently intact.

**Tech Stack:** Python 3.12, Ultralytics YOLO26n, official ByteTrack, PyTorch/CUDA, OpenCV, FFmpeg/ffprobe, Pillow, FastAPI, SQLAlchemy/SQLite, React/TypeScript/Vite.

**Spec:** docs/superpowers/specs/2026-09-09-v1-offline-person-tracking-design.md

**Status:** Revised after Astra audit on 2026-09-09. No implementation or acceptance gate is claimed complete by this document.

## Global constraints

- Local MP4; only COCO person class 0. No RTSP, live video, archive downloading, hands, pose, roles, actions, cash, transactions or training runtime.
- Use yolo26n.pt, imgsz=960 initially, explicit tracker="bytetrack.yaml", persist=True. Use the installed official configuration unchanged for the first baseline.
- One tracker instance per clip, maintained across all consecutive decoded frames. Do not drop frames or share tracker state between videos.
- Preserve original source bytes; draw on source-resolution frames. Produce H.264/yuv420p MP4. Annotated output is silent; original remains available.
- Local IDs are tracks within the clip, not identities or an exact distinct-person count.
- Resolve execution device before inference; report actual model execution, not just torch.cuda.is_available(). CPU fallback is diagnostic and cannot pass the RTX 3060 Ti gate.
- Video remains local. Download weights only from the official Ultralytics distribution; record artifact hash and tested package/runtime versions.
- Retain the spec's licensing notice in the user guide.
- Use a new codex/ branch/worktree based on the approved repository. Preserve the unrelated observation worktree and any user changes.
- Deliver observable evidence at each gate. Do not build dependent API/UI while real tracking or playback remains unproven.

## Audit resolutions

| Previous problem | Resolution |
| --- | --- |
| GPU/video acceptance at the end | Real model and output gates are Tasks 1–2 |
| CUDA availability displayed as GPU execution | Capture device after successful model inference |
| MIME and file existence treated as output validation | Probe, fully decode, play and seek the actual output |
| Imported fixture passed to queued-only processor | Test complete import/submission/state transitions |
| Missing metrics | Explicit frame counts, track counts, inference samples and end-to-end time |
| Removal of legacy API breaks retained tests | Separate app.v1.api entry point; retain old API |
| Retail/default tracker configurations mixed | Official bytetrack.yaml first; no old threshold edits |
| Launcher loses runtime discovery | Dedicated V1 launcher retains verified runtime discovery |

## Shared contracts

Create these in backend/app/v1/contracts.py. Internal source/output paths never enter public JSON.

```python
from dataclasses import dataclass
from enum import StrEnum

class JobStatus(StrEnum):
    IMPORTED = "imported"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

class Stage(StrEnum):
    IMPORTED = "imported"
    QUEUED = "queued"
    LOADING = "loading"
    TRACKING = "tracking"
    ENCODING = "encoding"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"

@dataclass(frozen=True)
class VideoMetadata:
    size_bytes: int
    width: int
    height: int
    duration_ms: int
    fps_num: int
    fps_den: int
    frame_count_estimate: int | None
    codec: str
    preview_supported: bool

@dataclass(frozen=True)
class TrackedPerson:
    track_id: int
    confidence: float
    xyxy: tuple[float, float, float, float]  # original-frame pixels

@dataclass(frozen=True)
class FrameTracking:
    people: list[TrackedPerson]
    inference_ms: float | None
    tracking_wall_ms: float

@dataclass(frozen=True)
class RunOptions:
    model_path: str
    device: str = "auto"
    image_size: int = 960

@dataclass(frozen=True)
class Progress:
    stage: Stage
    processed_frames: int
    total_frames_estimate: int | None

@dataclass(frozen=True)
class RunSummary:
    actual_device: str
    device_name: str
    processed_frames: int
    local_track_count: int
    inference_samples: int
    mean_inference_ms: float | None
    tracking_wall_ms_total: float
    processing_seconds: float
    effective_fps: float
    output_duration_ms: int
```

- probe_video(path: Path) -> VideoMetadata.
- PersonTracker(model_path: Path, device: str, image_size: int = 960); track_frame(frame: numpy.ndarray) -> FrameTracking; actual_device/device_name are established after successful inference.
- annotate_people(frame: numpy.ndarray, people: list[TrackedPerson]) -> numpy.ndarray.
- process_video(source: Path, output: Path, options: RunOptions, on_progress: Callable[[Progress], None]) -> RunSummary.
- VideoStore.import_mp4(upload: UploadFile) -> ImportedVideo. ImportedVideo has id: str (server UUID), original_name: str, source: Path, metadata: VideoMetadata.
- TrackingWorker.start() -> None; stop() -> None; submit(job_id: str) -> JobView. It invokes the same process_video used by the CLI.
- JobView is a Pydantic response with id, original_name, status, stage, metadata, processed_frames, total_frames_estimate, tracking_percent, summary, failure_code, source_url, result_url, created_at, started_at, finished_at. Nullable values use null, not fabricated defaults. Timestamps use UTC ISO format.
- Persist JobView fields plus private paths in a separate V1 database. Only READY exposes result_url.

Mean inference uses model-reported inference timing and its sample count; unavailable timing remains null. Full track-call wall time includes host result materialization. processing_seconds includes model loading, decode, tracking, encoding and validation; effective_fps = processed_frames / processing_seconds.

tracking_percent describes tracking only. When based on estimated frames label it estimated and cap below 100 until validation. Encoding/validation show stage names. Upload progress is separate.

## Files and dependency boundaries

| Files | Owner / purpose |
| --- | --- |
| backend/app/v1/contracts.py, settings.py | T1: shared contracts and independent settings |
| backend/app/v1/media.py, tracker.py, pipeline.py, cli.py | T1 processing; T2 validation/failure hardening |
| backend/tests/v1/conftest.py, test_tracker.py, test_media.py, test_pipeline.py | T1–T2 fixtures and meaningful tests |
| backend/app/v1/storage.py, jobs.py, worker.py, api.py | T3 upload, private DB, queue and serving |
| backend/tests/v1/test_jobs.py, test_api.py | T3 lifecycle/API tests |
| frontend/src/trackingApi.ts, VideoImport.tsx, TrackingResult.tsx | T4 new V1 components/helpers |
| frontend/src/App.tsx, styles.css, App.test.tsx | T4 composition; retain legacy api.ts exports/tests |
| start-v1.bat, scripts/start-v1.ps1 | T5 isolated launcher |
| docs/v1-acceptance.md, docs/v1-user-guide.md | Evidence from T1 onward; T5 operator instructions |

Dependency/configuration edits belong to the task requiring them: backend/pyproject.toml, frontend/package.json and lockfile, .gitignore. Ignore private media, models and V1 database before creating them. Do not commit recordings.

Before execution inspect applicable AGENTS.md, git status, available runtimes and baseline tests. Record pre-existing failures separately. Verify actual installed Ultralytics support for YOLO26, Torch/CUDA, ffmpeg/ffprobe/libx264, and a local Unicode font. Record a reproducible tested dependency set; an existing broad version range is not verification.

## Task 1: Produce the first real tracking video

**Consumes:** Spec, shared contracts, local environment and an authorized shop clip.
**Produces:** CLI, processing service, annotated MP4, local frame/ID evidence, measured summary.

- [ ] Find a usable recording within the scoped project media. Preserve it. If absent, request one MP4; a synthetic fixture cannot pass this gate.
- [ ] Choose a 15–60 second excerpt with a person clearly visible continuously for at least five seconds. Record source hash, excerpt range and evaluation interval before tuning.
- [ ] Verify model loading and actual inference on the RTX 3060 Ti; record environment versions and device. Resolve auto/cpu/CUDA index once and pass that device into inference.
- [ ] Implement contracts, tracker adapter and CLI around process_video. Add focused tests for class filtering, missing IDs, valid original-pixel coordinates, and independent state per video.
- [ ] Use continuous official ByteTrack. Discard invalid boxes and missing IDs without inventing IDs; render “người #ID · confidence%” with Pillow and a tested Unicode font.
- [ ] Record local evidence per frame: index, source timestamp, boxes and IDs. Keep evidence outside public responses and Git.
- [ ] Pipe annotated frames into FFmpeg to avoid writing and re-encoding a full intermediate video. Use argument arrays, drain stderr safely, close input and wait for encoder completion.
- [ ] Preserve rational source frame rate for initial CFR support. Explicitly reject unsupported variable timing or odd dimensions before processing rather than silently distort time/resolution; document this initial input limitation.
- [ ] Process every decoded frame; keep model inference timing separate from tracking wall time and complete processing time.
- [ ] Run the actual CLI and inspect beginning, middle and end of output. CLI --help describes input/output/device/imgsz; reject input=output and existing output instead of overwriting.

Tracker tests use fixtures with real-shaped result boxes (class, confidence, xyxy, optional IDs), 640x360 frames and a controllable model factory. The actual acceptance run must load real weights.

```python
def test_missing_ids_do_not_create_fake_tracks(tracker_without_ids, frame):
    assert tracker_without_ids.track_frame(frame).people == []

def test_coordinates_are_original_pixels(person_tracker, frame):
    person = person_tracker.track_frame(frame).people[0]
    assert person.track_id == 7
    assert person.xyxy == (10.0, 20.0, 100.0, 200.0)
```

**Gate:** Deliver a playable real output with visible boxes/IDs and successful actual GPU execution evidence. On the preselected five-second fully visible interval require zero unexplained ID switches, no track loss exceeding 0.5 seconds, and no sustained box attached to the wrong person. Record misses elsewhere too. These are clip-specific minimums, not universal accuracy promises. Failure is investigated here before building app plumbing.

## Task 2: Verify tracking quality and video playback

**Consumes:** T1 pipeline and actual output.
**Produces:** Validated processing, repeatable acceptance evidence and failure handling.

- [ ] Probe and fully decode input/output: H.264/yuv420p, same width/height, decoded output frame count equals decoded input, duration difference <= one source frame + 50 ms.
- [ ] Add validate_output(source_metadata, decoded_input_frames, output) -> VideoMetadata and call it before successful process_video return.
- [ ] Test corrupt MP4, missing/unreadable model, encoder failure, zero frames and truncated decode. A premature decoder stop must not silently produce success. Preserve the source hash on every failure.
- [ ] Check frame/timestamp coverage against the input metadata; log discrepancies and reject unexplained truncation.
- [ ] Open the actual output in a browser; play and seek at beginning, middle and end. Inspect Unicode labels and frame/box alignment.
- [ ] Evaluate two additional shop excerpts, including multiple people or occlusion where available. Record misses and ID switches; distinguish full exit/re-entry from within-view ID loss. Missing clips mean incomplete coverage.
- [ ] Verify a second video gets fresh tracker state and all timing denominators match the contracts.

Real media integration fixtures create three 640x360 frames, encode through actual FFmpeg and fully decode the result. Do not replace this with a filename or MIME assertion.

```python
def test_real_encoded_output(encoded_three_frame_result):
    result = encoded_three_frame_result
    assert (result.codec, result.pixel_format) == ("h264", "yuv420p")
    assert (result.width, result.height, result.decoded_frames) == (640, 360, 3)
```

**Gate:** T1 tracking criteria still pass; media checks and browser playback pass. Record actual seconds, FPS and model timing. Review evidence before Task 3.

## Task 3: Add local import and reliable job processing

**Consumes:** T2 process_video and shared contracts.
**Produces:** app.v1.api:app, persistent jobs and safe media endpoints.

- [ ] Implement separate V1 settings/database/API. Do not import the legacy live API at V1 startup; retain its tests and entry point.
- [ ] Stream imports with server UUID paths, actual byte counts and configurable size limit (initial 4 GiB). Validate readable media, duration and supported format; extension alone is insufficient.
- [ ] Handle disk/write/probe failure; remove only that failed import's validated private temporary files. Persist IMPORTED after successful validation. Preserve imported source on later AI failure.
- [ ] POST /api/v1/jobs accepts multipart video and returns 201 JobView. POST /api/v1/jobs/{id}/start returns 202 for newly/existing queued or processing jobs; completed/failed jobs return neutral 409.
- [ ] Add GET job status, /source, /result, and /result?download=1. Results require READY; validate resolved regular files beneath that exact job directory. Test range requests and download disposition.
- [ ] Atomically enqueue each imported job once. A single worker uses its own database sessions and invokes the proven process_video service sequentially.
- [ ] Wire worker.start/stop into lifespan. Stop prevents new submissions and completes or cleanly interrupts owned work. Recover queued jobs once after restart; mark interrupted PROCESSING jobs FAILED with xu_ly_bi_gian_doan.
- [ ] Persist stages/progress at bounded intervals and publish validated output atomically. Only successful validation can set READY.
- [ ] Health returns service="v1-person-tracking", version, dependency/asset readiness and configured device mode. A completed inference summary supplies actual execution evidence.
- [ ] Test complete lifecycle, duplicate start, serial jobs, restart recovery, missing result, safe failures and source preservation. Keep legacy tests meaningful and intact.

API fixtures use a temporary V1 database, real tiny MP4 and a controllable worker. imported_job starts as IMPORTED; the endpoint performs queuing before the worker processes it.

```python
def test_duplicate_start_queues_once(client, imported_job, controlled_worker):
    url = f"/api/v1/jobs/{imported_job.id}/start"
    assert client.post(url).status_code == 202
    assert client.post(url).status_code == 202
    assert controlled_worker.queued_ids == [imported_job.id]
```

**Gate:** Import the proven real clip through HTTP and obtain validated tracking output, truthful stages and playable/downloadable result.

## Task 4: Add the focused Vietnamese web flow

**Consumes:** T3 endpoints and exact JobView schema.
**Produces:** File selection/drop -> explicit start -> progress -> original/result playback and download.

- [ ] Add typed trackingApi.ts; keep legacy api.ts exports used by old tests/components.
- [ ] Implement Chọn video MP4/drop, local filename/size/upload progress, then server-confirmed dimensions/duration and Bắt đầu theo dõi người. Do not claim metadata is confirmed before probing.
- [ ] Disable conflicting actions and duplicate submissions; poll only the active job and cancel stale requests on job change/unmount.
- [ ] Persist active job ID locally; restore after reload and handle missing jobs without an endless spinner.
- [ ] Show loading/tracking/encoding/validation separately. Explain unsupported source preview; never present the original player as annotated output.
- [ ] After READY show both players, replay/download, actual device, processed frames, “Số ID trong clip”, elapsed seconds, effective FPS and measured mean inference if available.
- [ ] Test actual interaction sequence with mocked API stages, import/error/retry/refresh and stale responses. Use installed fireEvent or explicitly add user-event and lockfile; do not assume a ready job on initial render.
- [ ] Run frontend tests/build, then verify upload, progress, playback/seek and download with actual backend/video in a browser.

**Gate:** A nontechnical user completes the real workflow without a terminal; mocked tests alone cannot satisfy acceptance.

## Task 5: Package startup and complete acceptance

**Consumes:** Verified T4 application.
**Produces:** start-v1.bat, startup helper, user guide and actual acceptance record.

- [ ] Preserve start-local.bat for the old app; add start-v1.bat invoking scripts/start-v1.ps1.
- [ ] Resolve Python/pnpm using project environments, installed tools and verified bundled fallback paths. Use explicit working directories and test the resolved executables.
- [ ] Check dependencies/weights, start only loopback V1 services and wait for actual readiness before opening the page. On occupied ports verify service identity; never open stale legacy UI or kill unrelated processes.
- [ ] Start helper services hidden; provide readable launch errors and diagnostic logs. Repeat launch reuses only a verified matching instance.
- [ ] Document MP4 export/import, initial unsupported formats, source retention, track ID limits, playback/download and stopping V1.
- [ ] Run appropriate backend tests and frontend tests/build; record exact commands/results and separately identified pre-existing failures.
- [ ] Double-click launcher and repeat real workflow on RTX 3060 Ti. Check repeat launch and restarting while a job is active.
- [ ] Complete evidence for three shop excerpts: source/output duration/resolution, real execution device, timings, track continuity, browser playback. Missing evidence remains pending.
- [ ] Hand off launcher, runnable location and example tracked video. Do not automatically merge or publish the branch.

**Gate:** Startup, real tracking, validated playback, truthful progress and recovery all pass. CPU diagnosis or automated tests alone do not mean V1 is complete.

## Verification record and execution rules

For each task write base/head commit, files, exact commands, observed results, artifact locations, gate verdict and limitations into docs/v1-acceptance.md. Private media remains ignored. Do not invent measurements or substitute demo/mock results for real shop footage.

Before implementation the controller checks contracts/dependencies and supplies one implementer the relevant task and shared contracts. Tests must fail for the intended missing behavior before implementation, then pass after it. Review spec compliance and code quality independently, commit verified task changes, and proceed only when its gate passes. Fix failed gates in the same task.

## Self-review

- Approved offline person-only scope is preserved.
- Real model/video risk is tested before API/UI work.
- CLI and web share one processing service.
- GPU execution, model inference and total processing time are separate facts.
- Unsupported-input limits and missing evidence are explicit.
- Legacy startup/API/tests remain intact; V1 starts separately.
- This revision records a plan, not completed implementation or measured accuracy.
