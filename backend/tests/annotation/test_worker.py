from __future__ import annotations

import time
import threading
from pathlib import Path
from uuid import uuid4

from app.annotation.contracts import (
    CameraSetupCreate,
    MediaView,
    Point,
    RegisterClip,
    ReleasePreparedMedia,
    RetryPreparation,
    RoiWrite,
)
from app.annotation.frames import FrameService
from app.annotation.worker import PreparationWorker
from app.annotation.media import PreparationCancelled
from app.v1.media import probe_video


def test_worker_recovers_durable_preparing_clip(tmp_path, make_numbered_source, repo, jobs):
    job_id = str(uuid4())
    source = make_numbered_source(
        tmp_path / "source-data" / "jobs" / job_id / "source.mp4", frames=6
    )
    job = jobs.create_imported(job_id, "worker.mp4", source, probe_video(source))
    clip = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=job.id))
    worker = PreparationWorker(repo, FrameService(repo.database.root, repo), poll_seconds=0.01)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = repo.get_clip(clip.id)
            if current.preparation_state != "preparing":
                break
            time.sleep(0.02)
        assert current.preparation_state == "ready"
        assert current.source_sha256
    finally:
        worker.stop()


def test_worker_marks_missing_source_failed(tmp_path, imported_job, repo):
    Path(repo.jobs.get_private(imported_job.id).source_path).unlink()
    clip = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=imported_job.id))
    worker = PreparationWorker(repo, FrameService(repo.database.root, repo))
    assert worker.run_once()
    failed = repo.get_clip(clip.id)
    assert failed.preparation_state == "failed"
    assert failed.source_state == "missing"
    assert failed.failure_code == "source_missing"


def test_startup_reconciliation_marks_interrupted_preparation_and_cleans_owned_staging(
    imported_job, repo
):
    clip = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=imported_job.id))
    staging = repo.database.root / "staging" / str(clip.id) / str(uuid4())
    staging.mkdir(parents=True)
    (staging / "partial.mkv").write_bytes(b"partial")
    worker = PreparationWorker(repo, FrameService(repo.database.root, repo))

    worker.reconcile_startup()

    recovered = repo.get_clip(clip.id)
    assert recovered.preparation_state == "failed"
    assert recovered.failure_code == "preparation_interrupted"
    assert not (repo.database.root / "staging" / str(clip.id)).exists()


def test_worker_stop_cancels_owned_preparation_without_leaving_a_thread(
    imported_job, repo, monkeypatch
):
    clip = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=imported_job.id))
    entered = threading.Event()

    def blocking_prepare(*_args, cancel_requested, **_kwargs):
        entered.set()
        while not cancel_requested():
            time.sleep(0.01)
        raise PreparationCancelled("shutdown requested")

    monkeypatch.setattr("app.annotation.worker.prepare_clip", blocking_prepare)
    worker = PreparationWorker(repo, FrameService(repo.database.root, repo), poll_seconds=0.01)
    worker.start()
    assert entered.wait(2)
    worker.stop()

    assert worker._thread is None
    recovered = repo.get_clip(clip.id)
    assert recovered.preparation_state == "failed"
    assert recovered.failure_code == "preparation_interrupted"


def test_repeated_release_failure_does_not_churn_clip_revisions(
    imported_job, repo, monkeypatch
):
    clip = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=imported_job.id))
    service = FrameService(repo.database.root, repo)
    repo.complete_preparation(
        clip.id,
        source_sha256="a" * 64,
        media=MediaView(frame_count=3, fps_num=25, fps_den=1, width=640, height=360, sample_aspect_ratio="1:1"),
        artifact_manifest={},
        prepared_bytes=1,
        generation_id=uuid4(),
    )
    ready = repo.get_clip(clip.id)
    repo.release_prepared_media(
        clip.id,
        ReleasePreparedMedia(operation_id=uuid4(), expected_clip_revision=ready.revision),
    )
    monkeypatch.setattr(service, "release_generation", lambda _id: (_ for _ in ()).throw(OSError("locked")))
    worker = PreparationWorker(repo, service)
    assert worker.run_once()
    first = repo.get_clip(clip.id)
    assert worker.run_once()
    second = repo.get_clip(clip.id)
    assert second.revision == first.revision
    assert second.failure_code == "prepared_release_failed"


def test_release_preserves_roi_and_unblocks_storage_quota(
    tmp_path, make_numbered_source, repo, jobs
):
    def register(name: str):
        job_id = str(uuid4())
        source = make_numbered_source(
            tmp_path / "source-data" / "jobs" / job_id / "source.mp4", frames=8
        )
        job = jobs.create_imported(job_id, name, source, probe_video(source))
        return source, repo.register_clip(
            RegisterClip(operation_id=uuid4(), source_job_id=job.id)
        )

    source_a, clip_a = register("a.mp4")
    _, clip_b = register("b.mp4")
    service = FrameService(repo.database.root, repo)
    worker = PreparationWorker(repo, service)
    assert worker.run_once()
    ready_a = repo.get_clip(clip_a.id)
    assert ready_a.preparation_state == "ready"
    service.prepared_limit_bytes = ready_a.prepared_bytes + ready_a.prepared_bytes // 2

    assert worker.run_once()
    failed_b = repo.get_clip(clip_b.id)
    assert failed_b.preparation_state == "failed"
    assert failed_b.failure_code == "annotation_storage_full"

    setup = repo.create_setup(CameraSetupCreate(operation_id=uuid4(), name="Quầy"))
    with_roi = repo.save_roi(
        ready_a.id,
        RoiWrite(
            operation_id=uuid4(),
            expected_clip_revision=ready_a.revision,
            camera_setup_id=setup.id,
            polygon=[Point(x=0.2, y=0.2), Point(x=0.7, y=0.2), Point(x=0.7, y=0.7)],
        ),
    )
    releasing = repo.release_prepared_media(
        with_roi.id,
        ReleasePreparedMedia(
            operation_id=uuid4(), expected_clip_revision=with_roi.revision
        ),
    )
    assert releasing.prepared_bytes == ready_a.prepared_bytes
    assert worker.run_once()
    released = repo.get_clip(with_roi.id)
    assert released.preparation_state == "released"
    assert released.prepared_bytes == 0
    assert released.roi == with_roi.roi
    assert released.source_sha256 == ready_a.source_sha256
    assert source_a.is_file()

    retrying_b = repo.retry_preparation(
        failed_b.id,
        RetryPreparation(
            operation_id=uuid4(), expected_clip_revision=failed_b.revision
        ),
    )
    assert retrying_b.preparation_state == "preparing"
    assert worker.run_once()
    assert repo.get_clip(clip_b.id).preparation_state == "ready"
