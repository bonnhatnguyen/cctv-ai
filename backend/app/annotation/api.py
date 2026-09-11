from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from .contracts import (
    CameraSetupCreate,
    CameraSetupListView,
    CameraSetupView,
    ClipListView,
    ClipView,
    RegisterClip,
    ReleasePreparedMedia,
    RetryPreparation,
    RoiWrite,
    StorageView,
    TemplateWrite,
)
from .database import root_fingerprint
from .frames import (
    FrameIntegrityError,
    FrameOutOfRange,
    FrameQueueFull,
    FrameRequestTimeout,
    FrameService,
    SourceChanged,
)
from .media import StorageFull
from .repository import (
    AnnotationRepository,
    ClipNotReady,
    NotFound,
    PayloadConflict,
    RevisionConflict,
    SourceUnavailable,
)
from .worker import PreparationWorker


def _raise_domain_error(exc: Exception) -> None:
    if isinstance(exc, NotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "annotation_not_found") from exc
    if isinstance(exc, FrameOutOfRange):
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "frame_out_of_range") from exc
    if isinstance(exc, (RevisionConflict, PayloadConflict, ClipNotReady)):
        raise HTTPException(status.HTTP_409_CONFLICT, "annotation_conflict") from exc
    if isinstance(exc, (SourceUnavailable, SourceChanged)):
        raise HTTPException(status.HTTP_409_CONFLICT, "source_unavailable") from exc
    if isinstance(exc, FrameQueueFull):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "frame_queue_full") from exc
    if isinstance(exc, FrameRequestTimeout):
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "frame_timeout") from exc
    if isinstance(exc, StorageFull):
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, "annotation_storage_full") from exc
    if isinstance(exc, FrameIntegrityError):
        raise HTTPException(status.HTTP_409_CONFLICT, "prepared_media_invalid") from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found") from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_annotation_request") from exc
    raise exc


def _range_bounds(header: str | None, size: int) -> tuple[int, int, bool]:
    if not header:
        return 0, size - 1, False
    if not header.startswith("bytes=") or "," in header:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "invalid_range")
    raw_start, separator, raw_end = header[6:].partition("-")
    if not separator:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "invalid_range")
    try:
        if raw_start:
            start = int(raw_start)
            end = int(raw_end) if raw_end else size - 1
        else:
            suffix = int(raw_end)
            if suffix <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size - 1
    except ValueError as exc:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "invalid_range") from exc
    if start < 0 or end < start or start >= size:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "invalid_range")
    return start, min(end, size - 1), True


def _stream_leased_file(
    lease: AbstractContextManager[Path], path: Path, start: int, end: int
) -> Iterator[bytes]:
    try:
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    raise OSError("preview ended before the requested byte range")
                remaining -= len(block)
                yield block
    finally:
        lease.__exit__(None, None, None)


def create_router(
    repository: AnnotationRepository,
    frames: FrameService,
    worker: PreparationWorker,
    *,
    instance_id: str | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/annotations", tags=["basket-annotations"])

    @router.post("/clips", response_model=ClipView, status_code=status.HTTP_201_CREATED)
    def register_clip(request: RegisterClip, response: Response) -> ClipView:
        try:
            existing = repository.find_clip_by_source_job(request.source_job_id)
            result = repository.register_clip(request)
            if existing is not None:
                response.status_code = status.HTTP_200_OK
            worker.wake()
            return result
        except Exception as exc:
            _raise_domain_error(exc)

    @router.get("/clips", response_model=ClipListView)
    def list_clips(limit: int = Query(50, ge=1, le=100), cursor: str | None = None):
        try:
            return repository.list_clips(limit, cursor)
        except Exception as exc:
            _raise_domain_error(exc)

    @router.get("/clips/{clip_id}", response_model=ClipView)
    def get_clip(clip_id: UUID):
        try:
            return repository.get_clip(clip_id)
        except Exception as exc:
            _raise_domain_error(exc)

    @router.post("/clips/{clip_id}/retry", response_model=ClipView, status_code=status.HTTP_202_ACCEPTED)
    def retry_clip(clip_id: UUID, request: RetryPreparation):
        try:
            result = repository.retry_preparation(clip_id, request)
            worker.wake()
            return result
        except Exception as exc:
            _raise_domain_error(exc)

    @router.post("/clips/{clip_id}/release", response_model=ClipView, status_code=status.HTTP_202_ACCEPTED)
    def release_clip(clip_id: UUID, request: ReleasePreparedMedia):
        try:
            result = repository.release_prepared_media(clip_id, request)
            worker.wake()
            return result
        except Exception as exc:
            _raise_domain_error(exc)

    @router.get("/storage", response_model=StorageView)
    def storage_view():
        return frames.storage()

    @router.get("/clips/{clip_id}/frames/{index}")
    def exact_frame(clip_id: UUID, index: int):
        if index < 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_frame_index")
        try:
            payload = frames.read_frame(clip_id, index)
            clip = repository.get_clip(clip_id)
            return Response(
                payload,
                media_type="image/png",
                headers={
                    "X-Frame-Index": str(index),
                    "X-Source-SHA256": clip.source_sha256 or "",
                    "Cache-Control": "private, no-store",
                },
            )
        except Exception as exc:
            _raise_domain_error(exc)

    @router.get("/clips/{clip_id}/preview")
    def preview(clip_id: UUID, request: Request):
        lease = frames.preview_lease(clip_id)
        try:
            path = lease.__enter__()
            size = path.stat().st_size
            start, end, partial = _range_bounds(request.headers.get("range"), size)
        except Exception as exc:
            lease.__exit__(type(exc), exc, exc.__traceback__)
            _raise_domain_error(exc)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Cache-Control": "private, no-store",
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(
            _stream_leased_file(lease, path, start, end),
            status_code=status.HTTP_206_PARTIAL_CONTENT if partial else status.HTTP_200_OK,
            media_type="video/mp4",
            headers=headers,
        )

    @router.post("/setups", response_model=CameraSetupView, status_code=status.HTTP_201_CREATED)
    def create_setup(request: CameraSetupCreate):
        try:
            return repository.create_setup(request)
        except Exception as exc:
            _raise_domain_error(exc)

    @router.get("/setups", response_model=CameraSetupListView)
    def list_setups(limit: int = Query(50, ge=1, le=100), cursor: str | None = None):
        try:
            return repository.list_setups(limit, cursor)
        except Exception as exc:
            _raise_domain_error(exc)

    @router.put("/setups/{setup_id}/template", response_model=CameraSetupView)
    def save_template(setup_id: UUID, request: TemplateWrite):
        try:
            return repository.save_template(setup_id, request)
        except Exception as exc:
            _raise_domain_error(exc)

    @router.put("/clips/{clip_id}/roi", response_model=ClipView)
    def save_roi(clip_id: UUID, request: RoiWrite):
        try:
            return repository.save_roi(clip_id, request)
        except Exception as exc:
            _raise_domain_error(exc)

    @router.get("/health")
    def health():
        return {
            "service": "basket-annotation",
            "schema_version": 1,
            "instance_id": instance_id,
            "data_root_fingerprint": root_fingerprint(repository.database.source_data_root),
            "ready": True,
        }

    return router
