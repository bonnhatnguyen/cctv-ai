"""Independent local API for V1 offline person tracking."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from .database import create_database
from .jobs import JobConflictError, JobNotFoundError, JobRepository, JobView
from .settings import V1Settings, get_settings
from .storage import ImportStorageError, UnsupportedVideoError, UploadTooLargeError, VideoStore
from .worker import TrackingWorker, WorkerStoppedError


def _safe_file(path_text: str, job_id: str, jobs_root: Path) -> Path:
    try:
        job_dir = (jobs_root / job_id).resolve(strict=True)
        path = Path(path_text).resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found") from exc
    if not path.is_file() or not path.is_relative_to(job_dir):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found")
    return path


def create_app(
    *,
    settings: V1Settings | None = None,
    worker_factory: Callable[[JobRepository], object] | None = None,
) -> FastAPI:
    configured = settings or get_settings()
    configured.data_dir.mkdir(parents=True, exist_ok=True)
    jobs_root = configured.data_dir / "jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)
    database = create_database(configured.effective_database_url)
    database.create_schema()
    repository = JobRepository(database.sessions)
    store = VideoStore(jobs_root, configured.max_upload_bytes)
    worker = worker_factory(repository) if worker_factory else TrackingWorker(
        repository,
        jobs_root,
        configured.model_path,
        configured.device,
        configured.image_size,
        progress_interval_seconds=configured.progress_interval_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker.start()
        try:
            yield
        finally:
            worker.stop()
            database.engine.dispose()

    application = FastAPI(title="V1 Offline Person Tracking", version="1", lifespan=lifespan)
    application.state.settings = configured
    application.state.database = database
    application.state.repository = repository
    application.state.store = store
    application.state.worker = worker

    @application.post("/api/v1/jobs", response_model=JobView, status_code=status.HTTP_201_CREATED)
    async def import_video(request: Request) -> JobView:
        try:
            imported = await store.import_multipart(
                request.stream(),
                request.headers.get("content-type", ""),
                request.headers.get("content-length"),
            )
            try:
                return repository.create_imported(
                    imported.id, imported.original_name, imported.source, imported.metadata
                )
            except Exception:
                shutil.rmtree(imported.source.parent, ignore_errors=True)
                raise
        except UploadTooLargeError as exc:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "tep_video_qua_lon") from exc
        except UnsupportedVideoError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "khong_the_doc_video") from exc
        except ImportStorageError as exc:
            raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, "khong_the_luu_video") from exc

    @application.get("/api/v1/jobs/{job_id}", response_model=JobView)
    def get_job(job_id: str) -> JobView:
        try:
            return repository.get(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found") from exc

    @application.post("/api/v1/jobs/{job_id}/start", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
    def start_job(job_id: str) -> JobView:
        try:
            return worker.submit(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found") from exc
        except JobConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "job_not_startable") from exc
        except WorkerStoppedError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "worker_unavailable") from exc

    @application.get("/api/v1/jobs/{job_id}/source")
    def source_media(job_id: str):
        try:
            private = repository.get_private(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found") from exc
        return FileResponse(_safe_file(private.source_path, job_id, jobs_root), media_type="video/mp4")

    @application.get("/api/v1/jobs/{job_id}/result")
    def result_media(job_id: str, download: bool = Query(False)):
        try:
            view = repository.get(job_id)
            private = repository.get_private(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found") from exc
        if view.status != "ready":
            raise HTTPException(status.HTTP_409_CONFLICT, "result_not_ready")
        path = _safe_file(private.output_path, job_id, jobs_root)
        if download:
            stem = Path(view.original_name).stem or "video"
            return FileResponse(
                path,
                media_type="video/mp4",
                filename=f"{stem}-tracked.mp4",
                content_disposition_type="attachment",
            )
        return FileResponse(path, media_type="video/mp4")

    @application.get("/api/v1/health")
    def health():
        summary = repository.latest_completed_summary()
        dependencies = {"ffmpeg": shutil.which("ffmpeg") is not None, "ffprobe": shutil.which("ffprobe") is not None}
        assets = {"model": configured.model_path.is_file()}
        return {
            "service": "v1-person-tracking",
            "version": "1",
            "ready": all(dependencies.values()) and all(assets.values()),
            "dependencies": dependencies,
            "assets": assets,
            "configured_device": configured.device,
            "last_completed_inference": asdict(summary) if summary else None,
        }

    return application


app = create_app()
