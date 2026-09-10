from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from app.v1.api import create_app
from app.v1.contracts import RunSummary
from app.v1.jobs import JobNotFoundError, JobRepository
from app.v1.media import probe_video
from app.v1.settings import V1Settings
from app.v1.storage import ImportedVideo, VideoStore


class ControlledWorker:
    def __init__(self, repository: JobRepository):
        self.repository = repository
        self.queued_ids: list[str] = []
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def submit(self, job_id: str):
        view, newly_queued = self.repository.enqueue(job_id)
        if newly_queued:
            self.queued_ids.append(job_id)
        return view


@pytest.fixture
def api(tmp_path):
    settings = V1Settings(
        data_dir=tmp_path / "private-data",
        model_path=tmp_path / "yolo26n.pt",
        device="cpu",
        max_upload_bytes=2 * 1024 * 1024,
    )
    app = create_app(settings=settings, worker_factory=ControlledWorker)
    with TestClient(app) as client:
        yield client, app


def _upload(client: TestClient, path: Path, name: str = "shop.mp4"):
    with path.open("rb") as handle:
        return client.post("/api/v1/jobs", files={"video": (name, handle, "video/mp4")})


def test_import_streams_validated_mp4_and_returns_no_private_paths(api, encoded_three_frame_video):
    client, app = api

    response = _upload(client, encoded_three_frame_video, "C:\\private\\shop.mp4")

    assert response.status_code == 201
    payload = response.json()
    assert payload["original_name"] == "shop.mp4"
    assert payload["metadata"]["size_bytes"] == encoded_three_frame_video.stat().st_size
    assert payload["metadata"]["duration_ms"] > 0
    assert payload["status"] == payload["stage"] == "imported"
    assert payload["source_url"].endswith("/source")
    assert payload["result_url"] is None
    assert "private-data" not in response.text


def test_invalid_or_oversized_upload_leaves_no_private_job_directory(api, tmp_path):
    client, app = api
    root = app.state.settings.data_dir / "jobs"

    invalid = client.post("/api/v1/jobs", files={"video": ("fake.mp4", b"not video", "video/mp4")})
    oversized_path = tmp_path / "large.mp4"
    oversized_path.write_bytes(b"x" * (app.state.settings.max_upload_bytes + 1))
    oversized = _upload(client, oversized_path)

    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "khong_the_doc_video"}
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "tep_video_qua_lon"}
    assert list(root.iterdir()) == []


def test_duplicate_start_queues_once(api, encoded_three_frame_video):
    client, app = api
    imported = _upload(client, encoded_three_frame_video).json()
    url = f"/api/v1/jobs/{imported['id']}/start"

    assert client.post(url).status_code == 202
    assert client.post(url).status_code == 202
    assert app.state.worker.queued_ids == [imported["id"]]


def test_source_and_ready_result_support_ranges_and_download(api, encoded_three_frame_video):
    client, app = api
    imported = _upload(client, encoded_three_frame_video).json()
    job_id = imported["id"]

    source = client.get(f"/api/v1/jobs/{job_id}/source", headers={"Range": "bytes=0-15"})
    assert source.status_code == 206
    assert source.headers["content-range"].startswith("bytes 0-15/")
    assert source.headers["accept-ranges"] == "bytes"
    assert len(source.content) == 16
    assert client.get(f"/api/v1/jobs/{job_id}/result").status_code == 409

    repository = app.state.repository
    private = repository.get_private(job_id)
    result_path = Path(private.output_path)
    shutil.copyfile(encoded_three_frame_video, result_path)
    repository.enqueue(job_id)
    repository.mark_processing(job_id)
    repository.complete(job_id, result_path, RunSummary(
        actual_device="cuda:0", device_name="NVIDIA GeForce RTX 3060 Ti",
        processed_frames=3, local_track_count=1, inference_samples=3,
        mean_inference_ms=4.5, tracking_wall_ms_total=15.0,
        processing_seconds=0.25, effective_fps=12.0, output_duration_ms=120,
    ))

    inline = client.get(f"/api/v1/jobs/{job_id}/result", headers={"Range": "bytes=2-11"})
    download = client.get(f"/api/v1/jobs/{job_id}/result?download=1")
    assert inline.status_code == 206
    assert inline.headers["content-type"].startswith("video/mp4")
    assert inline.headers["content-range"].startswith("bytes 2-11/")
    assert "attachment" not in inline.headers.get("content-disposition", "")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")


def test_result_rejects_private_path_outside_exact_job_directory(api, encoded_three_frame_video, tmp_path):
    client, app = api
    imported = _upload(client, encoded_three_frame_video).json()
    job_id = imported["id"]
    outside = tmp_path / "outside.mp4"
    shutil.copyfile(encoded_three_frame_video, outside)
    repository = app.state.repository
    repository.enqueue(job_id)
    repository.mark_processing(job_id)
    repository.complete(job_id, outside, RunSummary(
        actual_device="cpu", device_name="CPU", processed_frames=3,
        local_track_count=0, inference_samples=0, mean_inference_ms=None,
        tracking_wall_ms_total=0.0, processing_seconds=1.0,
        effective_fps=3.0, output_duration_ms=120,
    ))

    response = client.get(f"/api/v1/jobs/{job_id}/result")

    assert response.status_code == 404
    assert str(outside) not in response.text


def test_health_identifies_isolated_service_and_reports_completed_execution(api, encoded_three_frame_video):
    client, app = api
    imported = _upload(client, encoded_three_frame_video).json()
    repository = app.state.repository
    private = repository.get_private(imported["id"])
    result = Path(private.output_path)
    shutil.copyfile(encoded_three_frame_video, result)
    repository.enqueue(imported["id"])
    repository.mark_processing(imported["id"])
    repository.complete(imported["id"], result, RunSummary(
        actual_device="cuda:0", device_name="NVIDIA GeForce RTX 3060 Ti",
        processed_frames=3, local_track_count=1, inference_samples=3,
        mean_inference_ms=4.5, tracking_wall_ms_total=15.0,
        processing_seconds=0.25, effective_fps=12.0, output_duration_ms=120,
    ))

    payload = client.get("/api/v1/health").json()

    assert payload["service"] == "v1-person-tracking"
    assert payload["version"] == "1"
    assert payload["configured_device"] == "cpu"
    assert payload["last_completed_inference"]["actual_device"] == "cuda:0"
    assert "model_path" not in payload


def test_lifespan_releases_sqlite_file_handle_on_windows(tmp_path):
    settings = V1Settings(data_dir=tmp_path / "disposable")
    app = create_app(settings=settings, worker_factory=ControlledWorker)

    with TestClient(app):
        assert (settings.data_dir / "jobs.db").is_file()

    database_file = settings.data_dir / "jobs.db"
    database_file.unlink()
    assert not database_file.exists()


def test_chunked_upload_without_content_length_stops_before_full_body_is_spooled(tmp_path):
    limit = 32 * 1024
    settings = V1Settings(data_dir=tmp_path / "bounded", max_upload_bytes=limit)
    app = create_app(settings=settings, worker_factory=ControlledWorker)
    boundary = b"task3-boundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="video"; filename="large.mp4"\r\n'
        b"Content-Type: video/mp4\r\n\r\n"
        + b"x" * (limit * 8)
        + b"\r\n--" + boundary + b"--\r\n"
    )
    chunks = [body[index:index + 4096] for index in range(0, len(body), 4096)]
    received_bytes = 0
    sent_messages = []

    async def receive():
        nonlocal received_bytes
        chunk = chunks.pop(0)
        received_bytes += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    async def send(message):
        sent_messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/v1/jobs",
        "raw_path": b"/api/v1/jobs", "query_string": b"", "root_path": "",
        "headers": [(b"host", b"test"), (b"content-type", b"multipart/form-data; boundary=" + boundary)],
        "client": ("test", 123), "server": ("test", 80),
    }

    try:
        asyncio.run(app(scope, receive, send))
    finally:
        app.state.database.engine.dispose()

    response_start = next(message for message in sent_messages if message["type"] == "http.response.start")
    assert response_start["status"] == 413
    assert received_bytes <= limit + 4096
    assert list((settings.data_dir / "jobs").iterdir()) == []


def test_oversized_content_length_is_rejected_as_413(api, encoded_three_frame_video):
    client, app = api
    declared_size = app.state.settings.max_upload_bytes + 64 * 1024 + 1

    with encoded_three_frame_video.open("rb") as handle:
        response = client.post(
            "/api/v1/jobs",
            files={"video": ("shop.mp4", handle, "video/mp4")},
            headers={"Content-Length": str(declared_size)},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "tep_video_qua_lon"}


def test_health_responds_while_uploaded_video_validation_is_blocked(
    tmp_path, encoded_three_frame_video, monkeypatch
):
    settings = V1Settings(data_dir=tmp_path / "concurrent")
    app = create_app(settings=settings, worker_factory=ControlledWorker)
    entered_validation = threading.Event()
    release_validation = threading.Event()
    original_validate = app.state.store._validate_and_publish

    def blocking_validate(*args):
        entered_validation.set()
        assert release_validation.wait(2)
        return original_validate(*args)

    monkeypatch.setattr(app.state.store, "_validate_and_publish", blocking_validate)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        watchdog = threading.Timer(1.0, release_validation.set)
        watchdog.start()
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                upload = asyncio.create_task(client.post(
                    "/api/v1/jobs",
                    files={"video": ("shop.mp4", encoded_three_frame_video.read_bytes(), "video/mp4")},
                ))
                while not entered_validation.is_set():
                    await asyncio.sleep(0.01)
                health = await client.get("/api/v1/health")
                health_elapsed = time.perf_counter() - started
                release_validation.set()
                uploaded = await upload
            return health, uploaded, health_elapsed
        finally:
            release_validation.set()
            watchdog.cancel()
            watchdog.join()

    try:
        health, uploaded, elapsed = asyncio.run(exercise())
    finally:
        app.state.database.engine.dispose()

    assert health.status_code == 200
    assert elapsed < 0.5
    assert uploaded.status_code == 201


def test_cancellation_between_upload_chunks_removes_partial_private_directory(tmp_path):
    root = tmp_path / "cancelled-stream"
    store = VideoStore(root, max_upload_bytes=1024 * 1024)
    boundary = b"cancel-boundary"
    first_chunk = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="video"; filename="shop.mp4"\r\n'
        b"Content-Type: video/mp4\r\n\r\npartial-video-bytes"
    )

    async def exercise():
        first_chunk_processed = anyio.Event()
        scope_ready = anyio.Event()
        holder = {}

        async def chunks():
            yield first_chunk
            first_chunk_processed.set()
            await anyio.sleep_forever()

        async def run_import():
            with anyio.CancelScope() as cancel_scope:
                holder["scope"] = cancel_scope
                scope_ready.set()
                await store.import_multipart(
                    chunks(), f"multipart/form-data; boundary={boundary.decode()}"
                )

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run_import)
            await scope_ready.wait()
            await first_chunk_processed.wait()
            holder["scope"].cancel()

    anyio.run(exercise)

    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_cancellation_after_publication_leaves_source_owned_by_database(
    tmp_path, encoded_three_frame_video, monkeypatch
):
    settings = V1Settings(data_dir=tmp_path / "cancelled-handoff")
    app = create_app(settings=settings, worker_factory=ControlledWorker)
    job_id = "cancelled-handoff-job"
    job_dir = settings.data_dir / "jobs" / job_id
    source = job_dir / "source.mp4"
    metadata = probe_video(encoded_three_frame_video)
    holder = {}

    async def publish_then_cancel(*_args, **_kwargs):
        job_dir.mkdir()
        shutil.copyfile(encoded_three_frame_video, source)
        holder["scope"].cancel()
        return ImportedVideo(job_id, "shop.mp4", source, metadata)

    monkeypatch.setattr(app.state.store, "import_multipart", publish_then_cancel)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with anyio.CancelScope() as cancel_scope:
                holder["scope"] = cancel_scope
                await client.post(
                    "/api/v1/jobs",
                    files={"video": ("shop.mp4", encoded_three_frame_video.read_bytes(), "video/mp4")},
                )

    try:
        anyio.run(exercise)
        try:
            stored = app.state.repository.get(job_id)
        except JobNotFoundError:
            stored = None
    finally:
        app.state.database.engine.dispose()

    assert source.is_file()
    assert stored is not None
    assert stored.status == "imported"
