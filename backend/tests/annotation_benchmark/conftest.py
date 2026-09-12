from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.annotation_benchmark.contracts import FrameSpan, SelectionItem


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--model-root",
        action="store",
        default=None,
        help="Private directory containing asset.json and verified model files",
    )


@dataclass
class PrivateFixture:
    source_root: Path
    annotation_root: Path
    jobs_path: Path
    annotation_path: Path
    source_path: Path
    source_job_id: UUID
    clip_id: UUID
    roi_id: UUID
    interaction_id: UUID
    action_id: UUID
    selection: list[SelectionItem]
    database: AnnotationDatabase
    repository: AnnotationRepository

    def logical_dump(self) -> str:
        payload: dict[str, list[list[object]]] = {}
        for name, path in (
            ("jobs", self.jobs_path),
            ("annotations", self.annotation_path),
        ):
            connection = sqlite3.connect(path)
            try:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                for (table,) in tables:
                    if table.startswith("sqlite_"):
                        continue
                    rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
                    payload[f"{name}.{table}"] = sorted(
                        [list(row) for row in rows], key=lambda row: repr(row)
                    )
            finally:
                connection.close()
        return json.dumps(payload, sort_keys=True, default=str)


@pytest.fixture
def private_fixture(tmp_path: Path) -> PrivateFixture:
    from app.annotation.contracts import (
        ActionAnnotationCreate,
        ActionMutation,
        CameraSetupCreate,
        InteractionCreate,
        MediaView,
        Point,
        RegisterClip,
        ReviewCoverageWrite,
        RoiWrite,
    )
    from app.annotation.database import AnnotationDatabase
    from app.annotation.repository import AnnotationRepository
    from app.v1.contracts import VideoMetadata
    from app.v1.database import create_database
    from app.v1.jobs import JobRepository

    source_root = (tmp_path / "source-data").resolve()
    annotation_root = (source_root / "annotations").resolve()
    source_root.mkdir()
    jobs_path = source_root / "jobs.db"
    v1_database = create_database(f"sqlite:///{jobs_path.as_posix()}")
    v1_database.create_schema()
    jobs = JobRepository(v1_database.sessions)

    job_id = uuid4()
    source_path = source_root / "jobs" / str(job_id) / "source.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"immutable-private-video-fixture")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    jobs.create_imported(
        str(job_id),
        "shop.mp4",
        source_path,
        VideoMetadata(
            size_bytes=source_path.stat().st_size,
            width=640,
            height=360,
            duration_ms=4000,
            fps_num=25,
            fps_den=1,
            frame_count_estimate=100,
            codec="h264",
            preview_supported=True,
            sample_aspect_ratio="2:1",
        ),
    )

    database = AnnotationDatabase(annotation_root, source_root)
    database.initialize()
    repo = AnnotationRepository(database, jobs)
    clip = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=job_id))
    clip = repo.complete_preparation(
        clip.id,
        source_sha256=source_hash,
        media=MediaView(
            frame_count=100,
            fps_num=25,
            fps_den=1,
            width=640,
            height=360,
            sample_aspect_ratio="2:1",
        ),
        artifact_manifest={"schema_version": 1, "chunks": []},
        prepared_bytes=1,
    )
    setup = repo.create_setup(CameraSetupCreate(operation_id=uuid4(), name="Quay 1"))
    clip = repo.save_roi(
        clip.id,
        RoiWrite(
            operation_id=uuid4(),
            expected_clip_revision=clip.revision,
            camera_setup_id=setup.id,
            polygon=[
                Point(x=0.4, y=0.4),
                Point(x=0.6, y=0.4),
                Point(x=0.6, y=0.6),
                Point(x=0.4, y=0.6),
            ],
        ),
    )
    workspace = repo.create_interaction(
        clip.id,
        InteractionCreate(
            operation_id=uuid4(),
            expected_clip_revision=clip.revision,
            hand="unknown",
        ),
    )
    interaction = workspace.interactions[0]
    workspace = repo.create_action(
        clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(),
            expected_clip_revision=workspace.clip_revision,
            interaction_id=interaction.id,
            label="hand_in",
            start_frame=10,
            end_frame=20,
            crossing_frame=15,
            object_kind="unknown",
            visibility="clear",
        ),
    )
    action = workspace.annotations[0]
    workspace = repo.confirm_action(
        clip.id,
        action.id,
        ActionMutation(
            operation_id=uuid4(),
            expected_clip_revision=workspace.clip_revision,
            expected_annotation_revision=action.revision,
        ),
    )
    workspace = repo.create_review_coverage(
        clip.id,
        ReviewCoverageWrite(
            operation_id=uuid4(),
            expected_clip_revision=workspace.clip_revision,
            start_frame=0,
            end_frame=30,
            reviewed_labels=["hand_in"],
        ),
    )
    fixture = PrivateFixture(
        source_root=source_root,
        annotation_root=annotation_root,
        jobs_path=jobs_path,
        annotation_path=database.path,
        source_path=source_path,
        source_job_id=job_id,
        clip_id=clip.id,
        roi_id=workspace.roi_revision_id,
        interaction_id=interaction.id,
        action_id=action.id,
        selection=[
            SelectionItem(
                segment_id=uuid4(),
                clip_id=clip.id,
                span=FrameSpan(start_frame=0, end_frame=30),
                partition="exploratory",
                parent_recording_id=None,
                recording_days=(),
                provenance_confirmed=False,
                scenario_tags=("entry",),
            )
        ],
        database=database,
        repository=repo,
    )
    try:
        yield fixture
    finally:
        database.close()
        v1_database.engine.dispose()
