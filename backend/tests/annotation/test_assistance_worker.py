from __future__ import annotations

from uuid import uuid4
import json

import pytest

from app.annotation.assistance_protocol import AssistanceChildResult
from app.annotation.assistance_store import AssistanceRepository
from app.annotation.assistance_worker import (
    AssistanceExecutionError,
    AssistanceWorker,
)
from app.annotation.contracts import AssistanceRunCreate, Point, RoiWrite
from app.annotation.settings import AnnotationSettings
from app.annotation.frames import SourceChanged
from app.inference_lease import InferenceLease


def _ready(repo, registered_clip, setup):
    return repo.save_roi(
        registered_clip.id,
        RoiWrite(
            operation_id=uuid4(), expected_clip_revision=registered_clip.revision,
            camera_setup_id=setup.id,
            polygon=[Point(x=.4, y=.4), Point(x=.6, y=.4), Point(x=.6, y=.6)],
        ),
    )


class _Frames:
    def __init__(self, source):
        self.source = source

    def resolve_source_path(self, _clip):
        return self.source

    def verify_source_path(self, _clip):
        return self.source


def test_worker_publishes_only_a_complete_result(
    tmp_path, repo, registered_clip, setup, imported_job
):
    clip = _ready(repo, registered_clip, setup)
    store = AssistanceRepository(repo.database, stride=1)
    run = store.create_run(
        clip.id,
        AssistanceRunCreate(
            operation_id=uuid4(), expected_clip_revision=clip.revision,
            model="dino", start_frame=0, end_frame=2,
        ),
    )
    model_root = (tmp_path / "models")
    (model_root / "grounding-dino-tiny").mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fake")

    def execute(request):
        assert request.run_id == run.id
        return AssistanceChildResult(
            run_id=run.id,
            scheduled_frames=[0, 1, 2], observed_frames=[0, 1, 2], proposals=[],
        )

    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(
            root=repo.database.root, assistance_python=python,
            assistance_model_root=model_root, assistance_stride=1,
        ),
        execute=execute,
    )

    assert worker.run_once()
    assert store.get_run(run.id).status == "succeeded"


def test_worker_records_model_failure_instead_of_empty_success(
    tmp_path, repo, registered_clip, setup, imported_job
):
    clip = _ready(repo, registered_clip, setup)
    store = AssistanceRepository(repo.database)
    run = store.create_run(
        clip.id,
        AssistanceRunCreate(
            operation_id=uuid4(), expected_clip_revision=clip.revision,
            model="dino", start_frame=0, end_frame=2,
        ),
    )
    model_root = tmp_path / "models"
    (model_root / "grounding-dino-tiny").mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fake")

    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(
            root=repo.database.root, assistance_python=python,
            assistance_model_root=model_root,
        ),
        execute=lambda _request: (_ for _ in ()).throw(RuntimeError("oom")),
    )

    assert worker.run_once()
    failed = store.get_run(run.id)
    assert failed.status == "failed"
    assert failed.error_code == "out_of_memory"


def test_worker_rejects_a_child_result_with_the_wrong_schedule(
    tmp_path, repo, registered_clip, setup, imported_job
):
    clip = _ready(repo, registered_clip, setup)
    store = AssistanceRepository(repo.database, stride=1)
    run = store.create_run(
        clip.id,
        AssistanceRunCreate(
            operation_id=uuid4(), expected_clip_revision=clip.revision,
            model="dino", start_frame=0, end_frame=2,
        ),
    )
    model_root = tmp_path / "models"
    (model_root / "grounding-dino-tiny").mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fake")
    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(
            root=repo.database.root, assistance_python=python,
            assistance_model_root=model_root, assistance_stride=1,
        ),
        execute=lambda _request: AssistanceChildResult(
            run_id=run.id, scheduled_frames=[], observed_frames=[], proposals=[]
        ),
    )

    assert worker.run_once()
    failed = store.get_run(run.id)
    assert failed.status == "failed"
    assert failed.error_code == "invalid_model_output"


def test_worker_verifies_source_before_and_after_inference(
    tmp_path, repo, registered_clip, setup, imported_job
):
    class ChangingFrames(_Frames):
        def __init__(self, source):
            super().__init__(source)
            self.calls = 0

        def verify_source_path(self, _clip):
            self.calls += 1
            if self.calls == 2:
                raise SourceChanged("source changed")
            return self.source

    clip = _ready(repo, registered_clip, setup)
    store = AssistanceRepository(repo.database, stride=1)
    run = store.create_run(
        clip.id,
        AssistanceRunCreate(
            operation_id=uuid4(), expected_clip_revision=clip.revision,
            model="dino", start_frame=0, end_frame=2,
        ),
    )
    model_root = tmp_path / "models"
    (model_root / "grounding-dino-tiny").mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fake")
    frames = ChangingFrames(repo.jobs.get_private(imported_job.id).source_path)
    worker = AssistanceWorker(
        store, repo, frames,
        AnnotationSettings(
            root=repo.database.root, assistance_python=python,
            assistance_model_root=model_root, assistance_stride=1,
        ),
        execute=lambda _request: AssistanceChildResult(
            run_id=run.id, scheduled_frames=[0, 1, 2],
            observed_frames=[0, 1, 2], proposals=[]
        ),
    )

    assert worker.run_once()
    assert frames.calls == 2
    assert store.get_run(run.id).error_code == "source_changed"


def test_artifact_capacity_enforces_run_quota_and_disk_reserve(
    tmp_path, repo, imported_job, monkeypatch
):
    stage = tmp_path / "annotations" / "assistance" / "staging" / str(uuid4())
    stage.mkdir(parents=True)
    (stage / "stdout.log").write_bytes(b"x" * (64 * 1024 + 1))
    store = AssistanceRepository(repo.database)
    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(
            root=tmp_path / "annotations", assistance_output_limit_bytes=64 * 1024,
            free_disk_reserve_bytes=0,
        ),
        execute=lambda _request: None,
    )

    with pytest.raises(AssistanceExecutionError, match="artifact quota") as raised:
        worker._ensure_artifact_capacity(stage)
    assert raised.value.error_code == "artifact_quota_exceeded"


def test_artifact_capacity_counts_retained_runs(
    tmp_path, repo, imported_job
):
    annotation_root = tmp_path / "annotations"
    store = AssistanceRepository(repo.database)
    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(
            root=annotation_root, assistance_output_limit_bytes=64 * 1024,
            free_disk_reserve_bytes=0,
        ),
        execute=lambda _request: None,
    )
    root = worker._assistance_root()
    retained = root / "runs" / str(uuid4())
    retained.mkdir()
    (retained / "stdout.log").write_bytes(b"x" * (64 * 1024))
    stage = root / "staging" / str(uuid4())
    stage.mkdir()
    (stage / "request.json").write_bytes(b"{}")

    with pytest.raises(AssistanceExecutionError, match="artifact quota"):
        worker._ensure_artifact_capacity(stage)


def test_completed_stage_is_retained_with_a_checksum_manifest(
    tmp_path, repo, imported_job
):
    annotation_root = tmp_path / "annotations"
    store = AssistanceRepository(repo.database)
    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(root=annotation_root, free_disk_reserve_bytes=0),
        execute=lambda _request: None,
    )
    root = worker._assistance_root()
    run_id = uuid4()
    stage = root / "staging" / str(run_id)
    stage.mkdir()
    (stage / "request.json").write_text("{}", encoding="utf-8")

    worker._retain_stage(stage, root / "runs" / str(run_id))

    retained = root / "runs" / str(run_id)
    manifest = json.loads(
        (retained / "artifacts.sha256.json").read_text(encoding="utf-8")
    )
    assert "request.json" in manifest["files"]
    assert not stage.exists()


def test_startup_recovery_contains_one_corrupt_stage_and_continues(
    tmp_path, repo, imported_job, monkeypatch
):
    annotation_root = tmp_path / "annotations"
    store = AssistanceRepository(repo.database)
    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(root=annotation_root, free_disk_reserve_bytes=0),
        execute=lambda _request: None,
    )
    root = worker._assistance_root()
    bad = root / "staging" / "bad"
    good = root / "staging" / "good"
    bad.mkdir()
    good.mkdir()
    (bad / "request.json").write_text("{}", encoding="utf-8")
    (good / "request.json").write_text("{}", encoding="utf-8")
    retain = worker._retain_stage

    def retain_with_one_failure(stage, target):
        if stage.name == "bad":
            raise AssistanceExecutionError("artifact_root_invalid", "corrupt stage")
        retain(stage, target)

    monkeypatch.setattr(worker, "_retain_stage", retain_with_one_failure)

    worker._recover_staging()

    assert bad.exists()
    assert (root / "runs" / "good").is_dir()


def test_worker_waits_for_shared_inference_lease(
    tmp_path, repo, registered_clip, setup, imported_job
):
    import threading
    import time

    clip = _ready(repo, registered_clip, setup)
    store = AssistanceRepository(repo.database, stride=1)
    run = store.create_run(
        clip.id,
        AssistanceRunCreate(
            operation_id=uuid4(), expected_clip_revision=clip.revision,
            model="dino", start_frame=0, end_frame=2,
        ),
    )
    model_root = tmp_path / "models"
    (model_root / "grounding-dino-tiny").mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.write_bytes(b"fake")
    lease = InferenceLease(tmp_path / "shared.lock")
    executed = threading.Event()
    worker = AssistanceWorker(
        store, repo, _Frames(repo.jobs.get_private(imported_job.id).source_path),
        AnnotationSettings(root=repo.database.root, assistance_python=python,
                           assistance_model_root=model_root, assistance_stride=1),
        inference_lease=lease,
        execute=lambda _request: (
            executed.set()
            or AssistanceChildResult(run_id=run.id, scheduled_frames=[0, 1, 2],
                                     observed_frames=[0, 1, 2], proposals=[])
        ),
    )
    with lease.acquire():
        thread = threading.Thread(target=worker.run_once)
        thread.start()
        time.sleep(.15)
        assert not executed.is_set()
    thread.join(timeout=2)
    assert executed.is_set()
