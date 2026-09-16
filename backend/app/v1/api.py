"""Independent local API for V1 offline person tracking."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import anyio
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from app.annotation.api import create_router as create_annotation_router
from app.annotation.database import AnnotationDatabase
from app.annotation.frames import FrameService
from app.annotation.assistance_store import AssistanceRepository
from app.annotation.assistance_worker import AssistanceWorker
from app.annotation.repository import AnnotationRepository
from app.annotation.settings import AnnotationSettings, resolve_v1_data_dir
from app.annotation.worker import PreparationWorker
from app.live.api import live_router, router as live_webcam_router
from app.live.shutdown import router as system_shutdown_router
from app.live.webcam import webcam_service

from .database import create_database
from .jobs import JobConflictError, JobNotFoundError, JobRepository, JobView
from .settings import V1Settings, get_settings
from .storage import (
    ImportedVideo,
    ImportStorageError,
    UnsupportedVideoError,
    UploadTooLargeError,
    VideoStore,
)
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


def _persist_imported_or_rollback(
    repository: JobRepository, imported: ImportedVideo
) -> JobView:
    """Give a published source exactly one durable owner."""
    try:
        return repository.create_imported(
            imported.id,
            imported.original_name,
            imported.source,
            imported.metadata,
        )
    except BaseException:
        shutil.rmtree(imported.source.parent, ignore_errors=True)
        raise


async def _complete_import_handoff(
    repository: JobRepository, imported: ImportedVideo
) -> JobView:
    operation = asyncio.create_task(
        anyio.to_thread.run_sync(
            _persist_imported_or_rollback,
            repository,
            imported,
            abandon_on_cancel=False,
        )
    )
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        # asyncio.Task.cancel() bypasses AnyIO cancel-scope shielding. Keep the
        # operation handle alive and reconcile its outcome before propagating
        # cancellation; the synchronous operation itself owns rollback.
        with anyio.CancelScope(shield=True):
            try:
                await asyncio.shield(operation)
            except BaseException:
                pass
        raise


def create_app(
    *,
    settings: V1Settings | None = None,
    worker_factory: Callable[[JobRepository], object] | None = None,
    annotation_settings: AnnotationSettings | None = None,
    annotation_worker_factory: Callable[[AnnotationRepository, FrameService], object]
    | None = None,
    assistance_worker_factory: Callable[..., object] | None = None,
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
    source_data_root = resolve_v1_data_dir(configured.data_dir)
    configured_annotation = annotation_settings or AnnotationSettings.for_data_dir(
        source_data_root
    )
    if configured_annotation.root is None:
        configured_annotation = configured_annotation.model_copy(
            update={"root": (source_data_root / "annotations").resolve()}
        )
    annotation_database = AnnotationDatabase(configured_annotation.root, source_data_root)
    annotation_repository = AnnotationRepository(annotation_database, repository)
    annotation_frames = FrameService(
        configured_annotation.root,
        annotation_repository,
        cache_limit_bytes=configured_annotation.frame_cache_limit_bytes,
        queue_limit=configured_annotation.frame_queue_limit,
        request_deadline_seconds=configured_annotation.frame_request_deadline_seconds,
        prepared_limit_bytes=configured_annotation.prepared_limit_bytes,
        free_disk_reserve_bytes=configured_annotation.free_disk_reserve_bytes,
    )
    annotation_worker = (
        annotation_worker_factory(annotation_repository, annotation_frames)
        if annotation_worker_factory
        else PreparationWorker(
            annotation_repository,
            annotation_frames,
            stall_timeout_seconds=configured_annotation.process_stall_timeout_seconds,
            deadline_seconds=configured_annotation.preparation_deadline_seconds,
        )
    )
    from app.annotation.assistance_worker import assistance_asset_hashes

    assistance_store = AssistanceRepository(
        annotation_database,
        queue_limit=configured_annotation.assistance_queue_limit,
        stride=configured_annotation.assistance_stride,
        max_frames=configured_annotation.assistance_max_frames,
        dino_device=configured_annotation.assistance_dino_device,
        asset_hashes=assistance_asset_hashes(configured_annotation),
    )
    assistance_worker = (
        assistance_worker_factory(
            assistance_store, annotation_repository, annotation_frames, configured_annotation
        )
        if assistance_worker_factory
        else AssistanceWorker(
            assistance_store, annotation_repository, annotation_frames, configured_annotation
        )
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        annotation_database.initialize()
        reconcile_annotation = getattr(annotation_worker, "reconcile_startup", None)
        if callable(reconcile_annotation):
            reconcile_annotation()
        reconcile_assistance = getattr(assistance_worker, "reconcile_startup", None)
        if callable(reconcile_assistance):
            reconcile_assistance()
        worker.start()
        annotation_worker.start()
        assistance_worker.start()
        try:
            yield
        finally:
            errors: list[BaseException] = []
            for stop in (
                webcam_service.stop,
                assistance_worker.stop, annotation_worker.stop, worker.stop,
                annotation_database.close,
            ):
                try:
                    stop()
                except BaseException as exc:
                    errors.append(exc)
            database.engine.dispose()
            if errors:
                raise errors[0]

    application = FastAPI(title="V1 Offline Person Tracking", version="1", lifespan=lifespan)
    application.state.settings = configured
    application.state.database = database
    application.state.repository = repository
    application.state.store = store
    application.state.worker = worker
    application.state.annotation_settings = configured_annotation
    application.state.annotation_database = annotation_database
    application.state.annotation_repository = annotation_repository
    application.state.annotation_frames = annotation_frames
    application.state.annotation_worker = annotation_worker
    application.state.assistance_store = assistance_store
    application.state.assistance_worker = assistance_worker
    application.include_router(live_webcam_router)
    application.include_router(live_router)
    application.include_router(system_shutdown_router)
    application.include_router(
        create_annotation_router(
            annotation_repository,
            annotation_frames,
            annotation_worker,
            assistance_store,
            assistance_worker,
            instance_id=configured.instance_id,
        )
    )

    @application.post("/api/v1/jobs", response_model=JobView, status_code=status.HTTP_201_CREATED)
    async def import_video(request: Request) -> JobView:
        try:
            imported = await store.import_multipart(
                request.stream(),
                request.headers.get("content-type", ""),
                request.headers.get("content-length"),
            )
            # Once storage publishes source.mp4, either persist its owning row
            # or roll the directory back. Outer request cancellation is deferred
            # until this ownership handoff is complete.
            with anyio.CancelScope(shield=True):
                return await _complete_import_handoff(repository, imported)
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
            "instance_id": configured.instance_id,
            "ready": all(dependencies.values()) and all(assets.values()),
            "dependencies": dependencies,
            "assets": assets,
            "configured_device": configured.device,
            "last_completed_inference": asdict(summary) if summary else None,
        }

    return application


app = create_app()
