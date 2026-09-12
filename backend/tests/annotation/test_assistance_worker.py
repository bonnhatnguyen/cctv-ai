from __future__ import annotations

from uuid import uuid4

from app.annotation.assistance_protocol import AssistanceChildResult
from app.annotation.assistance_store import AssistanceRepository
from app.annotation.assistance_worker import AssistanceWorker
from app.annotation.contracts import AssistanceRunCreate, Point, RoiWrite
from app.annotation.settings import AnnotationSettings
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


def test_worker_publishes_only_a_complete_result(
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
    assert failed.error_code == "model_process_failed"


def test_worker_waits_for_shared_inference_lease(
    tmp_path, repo, registered_clip, setup, imported_job
):
    import threading
    import time

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
