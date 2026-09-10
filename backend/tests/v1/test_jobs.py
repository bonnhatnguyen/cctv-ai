from __future__ import annotations

import shutil
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from app.v1.contracts import Progress, RunSummary, Stage
from app.v1.database import create_database
from app.v1.jobs import JobConflictError, JobRepository
from app.v1.worker import TrackingWorker, WorkerStoppedError


def _summary(frames: int = 3) -> RunSummary:
    return RunSummary(
        actual_device="cpu",
        device_name="CPU",
        processed_frames=frames,
        local_track_count=1,
        inference_samples=frames,
        mean_inference_ms=4.5,
        tracking_wall_ms_total=15.0,
        processing_seconds=0.25,
        effective_fps=12.0,
        output_duration_ms=120,
    )


@pytest.fixture
def repository(tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    database.create_schema()
    return JobRepository(database.sessions)


def _import_job(repository: JobRepository, source: Path, job_id: str = "job-1"):
    from app.v1.media import probe_video

    return repository.create_imported(job_id, "shop.mp4", source, probe_video(source))


def test_job_view_never_exposes_private_paths(repository, encoded_three_frame_video, tmp_path):
    source = tmp_path / "job-1" / "source.mp4"
    source.parent.mkdir()
    shutil.copyfile(encoded_three_frame_video, source)

    payload = _import_job(repository, source).model_dump(mode="json")

    assert payload["status"] == "imported"
    assert payload["source_url"] == "/api/v1/jobs/job-1/source"
    assert payload["result_url"] is None
    assert not {"source_path", "output_path", "C:"}.intersection(payload)


def test_worker_processes_jobs_serially_and_publishes_only_completed_output(
    repository, encoded_three_frame_video, tmp_path
):
    active = 0
    max_active = 0
    call_order: list[str] = []
    lock = threading.Lock()

    def controlled_process(source, output, options, on_progress, **_kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        call_order.append(source.parent.name)
        on_progress(Progress(Stage.TRACKING, 1, 3))
        shutil.copyfile(source, output)
        with lock:
            active -= 1
        return _summary()

    for job_id in ("job-1", "job-2"):
        source = tmp_path / job_id / "source.mp4"
        source.parent.mkdir()
        shutil.copyfile(encoded_three_frame_video, source)
        _import_job(repository, source, job_id)

    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960, process=controlled_process)
    worker.start()
    worker.submit("job-1")
    worker.submit("job-2")
    assert worker.wait_until_idle(timeout=5)
    worker.stop()

    assert call_order == ["job-1", "job-2"]
    assert max_active == 1
    assert repository.get("job-1").status == "ready"
    assert repository.get("job-2").result_url == "/api/v1/jobs/job-2/result"
    assert (tmp_path / "job-1" / "annotated.mp4").is_file()
    assert not (tmp_path / "job-1" / "annotated.tmp.mp4").exists()


def test_worker_failure_is_safe_and_preserves_imported_source(repository, encoded_three_frame_video, tmp_path):
    source = tmp_path / "job-1" / "source.mp4"
    source.parent.mkdir()
    shutil.copyfile(encoded_three_frame_video, source)
    original = source.read_bytes()
    _import_job(repository, source)

    def failing_process(source, output, options, on_progress, **_kwargs):
        output.write_bytes(b"partial")
        raise OSError(f"private path: {source}")

    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960, process=failing_process)
    worker.start()
    worker.submit("job-1")
    assert worker.wait_until_idle(timeout=5)
    worker.stop()

    stored = repository.get("job-1")
    assert (stored.status, stored.stage, stored.failure_code) == (
        "failed", "failed", "khong_the_xu_ly_video"
    )
    assert source.read_bytes() == original
    assert not (tmp_path / "job-1" / "annotated.tmp.mp4").exists()


def test_restart_marks_interrupted_processing_failed_and_recovers_queued_once(
    repository, encoded_three_frame_video, tmp_path
):
    for job_id in ("processing", "queued"):
        source = tmp_path / job_id / "source.mp4"
        source.parent.mkdir()
        shutil.copyfile(encoded_three_frame_video, source)
        _import_job(repository, source, job_id)
        repository.enqueue(job_id)
    repository.mark_processing("processing")
    calls: list[str] = []

    def controlled_process(source, output, options, on_progress, **_kwargs):
        calls.append(source.parent.name)
        shutil.copyfile(source, output)
        return _summary()

    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960, process=controlled_process)
    worker.start()
    worker.start()
    assert worker.wait_until_idle(timeout=5)
    worker.stop()

    interrupted = repository.get("processing")
    assert interrupted.status == "failed"
    assert interrupted.failure_code == "xu_ly_bi_gian_doan"
    assert calls == ["queued"]


def test_restart_removes_only_private_temporary_files_of_interrupted_jobs(
    repository, encoded_three_frame_video, tmp_path
):
    for job_id in ("interrupted", "imported"):
        source = tmp_path / job_id / "source.mp4"
        source.parent.mkdir()
        shutil.copyfile(encoded_three_frame_video, source)
        _import_job(repository, source, job_id)
        for name in ("annotated.tmp.mp4", "tracking.tmp.evidence.jsonl",
                     "annotated.mp4", "tracking.evidence.jsonl", "other.tmp"):
            (source.parent / name).write_bytes(b"preserve unless private interrupted temp")
    repository.enqueue("interrupted")
    repository.mark_processing("interrupted")
    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960)
    worker.start()
    worker.stop()
    for name in ("annotated.tmp.mp4", "tracking.tmp.evidence.jsonl"):
        assert not (tmp_path / "interrupted" / name).exists()
        assert (tmp_path / "imported" / name).is_file()
    for name in ("source.mp4", "annotated.mp4", "tracking.evidence.jsonl", "other.tmp"):
        assert (tmp_path / "interrupted" / name).is_file()
    assert (tmp_path / "interrupted" / "source.mp4").read_bytes() == encoded_three_frame_video.read_bytes()
    assert repository.get("interrupted").failure_code == "xu_ly_bi_gian_doan"


def test_restart_does_not_clean_persisted_paths_outside_exact_job_directory(
    repository, encoded_three_frame_video, tmp_path
):
    source = tmp_path / "unrelated" / "source.mp4"
    source.parent.mkdir()
    shutil.copyfile(encoded_three_frame_video, source)
    _import_job(repository, source, "job-1")
    repository.enqueue("job-1")
    repository.mark_processing("job-1")
    temporary = source.parent / "annotated.tmp.mp4"
    temporary.write_bytes(b"unrelated")
    worker = TrackingWorker(repository, tmp_path / "owned-root", "model.pt", "cpu", 960)
    worker.start()
    worker.stop()
    assert temporary.read_bytes() == b"unrelated"
    assert repository.get("job-1").failure_code == "xu_ly_bi_gian_doan"


def test_submit_is_idempotent_for_active_jobs_and_rejects_terminal_or_stopped(
    repository, encoded_three_frame_video, tmp_path
):
    source = tmp_path / "job-1" / "source.mp4"
    source.parent.mkdir()
    shutil.copyfile(encoded_three_frame_video, source)
    _import_job(repository, source)
    release = threading.Event()

    def controlled_process(source, output, options, on_progress, **_kwargs):
        assert release.wait(5)
        shutil.copyfile(source, output)
        return _summary()

    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960, process=controlled_process)
    worker.start()
    assert worker.submit("job-1").status in {"queued", "processing"}
    assert worker.submit("job-1").status in {"queued", "processing"}
    release.set()
    assert worker.wait_until_idle(timeout=5)
    with pytest.raises(JobConflictError):
        worker.submit("job-1")
    worker.stop()
    with pytest.raises(WorkerStoppedError):
        worker.submit("missing")


def test_stop_cleanly_interrupts_owned_work_without_hanging(repository, encoded_three_frame_video, tmp_path):
    source = tmp_path / "job-1" / "source.mp4"
    source.parent.mkdir()
    shutil.copyfile(encoded_three_frame_video, source)
    _import_job(repository, source)
    started = threading.Event()
    release = threading.Event()
    interrupted = threading.Event()

    def controlled_process(source, output, options, on_progress, **_kwargs):
        started.set()
        try:
            while not release.wait(0.01):
                on_progress(Progress(Stage.TRACKING, 1, 3))
        except Exception:
            interrupted.set()
            raise
        shutil.copyfile(source, output)
        return _summary()

    worker = TrackingWorker(
        repository, tmp_path, "model.pt", "cpu", 960,
        process=controlled_process,
    )
    worker.start()
    worker.submit("job-1")
    assert started.wait(1)

    worker.stop()
    release.set()

    assert interrupted.wait(1)
    assert worker.wait_until_idle(timeout=1)
    stored = repository.get("job-1")
    assert stored.status == "failed"
    assert stored.failure_code == "xu_ly_bi_gian_doan"


def test_worker_contains_preprocessing_cleanup_failure_and_runs_next_job(
    repository, encoded_three_frame_video, tmp_path
):
    calls: list[str] = []
    for job_id in ("job-1", "job-2"):
        source = tmp_path / job_id / "source.mp4"
        source.parent.mkdir()
        shutil.copyfile(encoded_three_frame_video, source)
        _import_job(repository, source, job_id)
    # A directory at the temporary-file path reproduces Windows unlink failure
    # without relying on process-global monkeypatching.
    (tmp_path / "job-1" / "annotated.tmp.mp4").mkdir()

    def controlled_process(source, output, options, on_progress, **_kwargs):
        calls.append(source.parent.name)
        shutil.copyfile(source, output)
        return _summary()

    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960, process=controlled_process)
    worker.start()
    worker.submit("job-1")
    worker.submit("job-2")
    assert worker.wait_until_idle(timeout=2)
    worker.stop()

    assert repository.get("job-1").status == "failed"
    assert repository.get("job-2").status == "ready"
    assert calls == ["job-1", "job-2"]


def test_worker_contains_database_error_before_process_and_runs_next_job(
    repository, encoded_three_frame_video, tmp_path, monkeypatch
):
    for job_id in ("job-1", "job-2"):
        source = tmp_path / job_id / "source.mp4"
        source.parent.mkdir()
        shutil.copyfile(encoded_three_frame_video, source)
        _import_job(repository, source, job_id)
    original_mark_processing = repository.mark_processing
    failed_once = False

    def fail_first_mark(job_id):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("database temporarily unavailable")
        return original_mark_processing(job_id)

    monkeypatch.setattr(repository, "mark_processing", fail_first_mark)

    def controlled_process(source, output, options, on_progress, **_kwargs):
        shutil.copyfile(source, output)
        return _summary()

    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960, process=controlled_process)
    worker.start()
    worker.submit("job-1")
    worker.submit("job-2")
    assert worker.wait_until_idle(timeout=2)
    worker.stop()

    assert repository.get("job-1").status == "failed"
    assert repository.get("job-2").status == "ready"


def test_worker_retries_failure_persistence_without_losing_the_queue(
    repository, encoded_three_frame_video, tmp_path, monkeypatch
):
    for job_id in ("job-1", "job-2"):
        source = tmp_path / job_id / "source.mp4"
        source.parent.mkdir()
        shutil.copyfile(encoded_three_frame_video, source)
        _import_job(repository, source, job_id)
    original_fail = repository.fail
    failure_writes = 0

    def fail_once(job_id, code):
        nonlocal failure_writes
        failure_writes += 1
        if failure_writes == 1:
            raise OSError("database temporarily unavailable")
        return original_fail(job_id, code)

    monkeypatch.setattr(repository, "fail", fail_once)

    def controlled_process(source, output, options, on_progress, **_kwargs):
        if source.parent.name == "job-1":
            raise RuntimeError("inference failed")
        shutil.copyfile(source, output)
        return _summary()

    worker = TrackingWorker(repository, tmp_path, "model.pt", "cpu", 960, process=controlled_process)
    worker.start()
    worker.submit("job-1")
    worker.submit("job-2")
    assert worker.wait_until_idle(timeout=2)
    worker.stop()

    assert failure_writes == 2
    assert repository.get("job-1").status == "failed"
    assert repository.get("job-2").status == "ready"


def test_stop_waits_until_long_noncallback_work_is_fully_reaped(
    repository, encoded_three_frame_video, tmp_path
):
    source = tmp_path / "job-1" / "source.mp4"
    source.parent.mkdir()
    shutil.copyfile(encoded_three_frame_video, source)
    _import_job(repository, source)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def controlled_process(source, output, options, on_progress, **_kwargs):
        started.set()
        assert release.wait(2)
        shutil.copyfile(source, output)
        finished.set()
        return _summary()

    worker = TrackingWorker(
        repository, tmp_path, "model.pt", "cpu", 960,
        process=controlled_process,
    )
    worker.start()
    worker.submit("job-1")
    assert started.wait(1)
    timer = threading.Timer(0.25, release.set)
    timer.start()

    worker.stop()
    timer.join()

    assert finished.is_set()
    assert worker._thread is not None and not worker._thread.is_alive()
    assert repository.get("job-1").status == "ready"
