from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from app.annotation.contracts import Point, RegisterClip, RoiWrite, TemplateWrite
from app.annotation.database import AnnotationDatabase, DatabaseBindingError, SchemaVersionError
from app.annotation.repository import PayloadConflict, RevisionConflict
from app.annotation.settings import resolve_annotation_root


TRIANGLE = [Point(x=0.2, y=0.2), Point(x=0.6, y=0.2), Point(x=0.6, y=0.6)]


def test_register_clip_is_idempotent_for_source_job(repo, imported_job):
    first = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=imported_job.id))
    second = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=imported_job.id))
    assert second == first
    assert second.preparation_state == "preparing"
    assert second.media is None
    assert second.source_sha256 is None


def test_retry_is_idempotent_before_stale_revision_check(repo, registered_clip, setup):
    request = RoiWrite(
        operation_id=uuid4(),
        expected_clip_revision=registered_clip.revision,
        camera_setup_id=setup.id,
        polygon=TRIANGLE,
    )
    saved = repo.save_roi(registered_clip.id, request)
    assert repo.save_roi(registered_clip.id, request) == saved
    stale = request.model_copy(update={"operation_id": uuid4()})
    with pytest.raises(RevisionConflict):
        repo.save_roi(registered_clip.id, stale)


def test_reusing_operation_with_different_payload_is_rejected(repo, registered_clip, setup):
    operation_id = uuid4()
    request = RoiWrite(
        operation_id=operation_id,
        expected_clip_revision=registered_clip.revision,
        camera_setup_id=setup.id,
        polygon=TRIANGLE,
    )
    repo.save_roi(registered_clip.id, request)
    changed = request.model_copy(
        update={"polygon": [Point(x=0.1, y=0.1), Point(x=0.8, y=0.1), Point(x=0.8, y=0.8)]}
    )
    with pytest.raises(PayloadConflict):
        repo.save_roi(registered_clip.id, changed)


def test_concurrent_writers_allow_only_one_revision(repo, registered_clip, setup):
    def save(offset: float):
        return repo.save_roi(
            registered_clip.id,
            RoiWrite(
                operation_id=uuid4(),
                expected_clip_revision=registered_clip.revision,
                camera_setup_id=setup.id,
                polygon=[
                    Point(x=0.1 + offset, y=0.1),
                    Point(x=0.7, y=0.1),
                    Point(x=0.7, y=0.7),
                ],
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = []
        for future in [pool.submit(save, 0), pool.submit(save, 0.05)]:
            try:
                outcomes.append(future.result())
            except RevisionConflict as exc:
                outcomes.append(exc)
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, RevisionConflict) for item in outcomes) == 1


def test_template_update_does_not_change_existing_clip_snapshot(repo, registered_clip, setup):
    templated = repo.save_template(
        setup.id,
        TemplateWrite(
            operation_id=uuid4(), expected_setup_revision=setup.revision, polygon=TRIANGLE
        ),
    )
    saved = repo.save_roi(
        registered_clip.id,
        RoiWrite(
            operation_id=uuid4(),
            expected_clip_revision=registered_clip.revision,
            camera_setup_id=setup.id,
            polygon=templated.template.polygon,
            template_revision_id=templated.template.id,
        ),
    )
    before = saved.roi.model_dump_json()
    repo.save_template(
        setup.id,
        TemplateWrite(
            operation_id=uuid4(),
            expected_setup_revision=templated.revision,
            polygon=[Point(x=0.1, y=0.1), Point(x=0.9, y=0.1), Point(x=0.9, y=0.9)],
        ),
    )
    assert repo.get_clip(saved.id).roi.model_dump_json() == before
    assert repo.count_roi_revisions(saved.id) == 1


def test_data_roots_do_not_share_annotation_database(tmp_path: Path):
    a = resolve_annotation_root(tmp_path / "data-a", override=None)
    b = resolve_annotation_root(tmp_path / "data-b", override=None)
    assert a == (tmp_path / "data-a" / "annotations").resolve()
    assert b == (tmp_path / "data-b" / "annotations").resolve()
    assert a != b
    with pytest.raises(ValueError, match="absolute"):
        resolve_annotation_root(tmp_path / "data-a", override=Path("relative"))


def test_database_rejects_a_different_source_root_without_mutation(tmp_path: Path):
    annotation_root = tmp_path / "shared-annotations"
    first = AnnotationDatabase(annotation_root, tmp_path / "data-a")
    first.initialize()
    first.close()
    database_bytes = (annotation_root / "annotations.db").read_bytes()
    second = AnnotationDatabase(annotation_root, tmp_path / "data-b")
    with pytest.raises(DatabaseBindingError):
        second.initialize()
    assert (annotation_root / "annotations.db").read_bytes() == database_bytes


def test_roi_foreign_keys_are_enforced_after_reopen(tmp_path: Path):
    root = tmp_path / "annotations"
    source = tmp_path / "source"
    database = AnnotationDatabase(root, source)
    database.initialize()
    database.close()
    reopened = AnnotationDatabase(root, source)
    reopened.initialize()
    try:
        with pytest.raises(sqlite3.IntegrityError), reopened.write_transaction() as connection:
            connection.execute(
                """INSERT INTO roi_revisions(
                    id,kind,clip_id,camera_setup_id,revision,polygon_json,
                    template_revision_id,created_at
                ) VALUES(?,'clip',?,?,?,?,NULL,?)""",
                (str(uuid4()), str(uuid4()), str(uuid4()), 1, "[]", "now"),
            )
    finally:
        reopened.close()


def test_failed_migration_rolls_back_and_future_schema_is_rejected(tmp_path: Path):
    root = tmp_path / "annotations"
    broken = AnnotationDatabase(root, tmp_path / "source", before_version_write=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        broken.initialize()
    connection = sqlite3.connect(root / "annotations.db")
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    assert "annotation_clips" not in names

    root.mkdir(exist_ok=True)
    connection = sqlite3.connect(root / "annotations.db")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_migrations(version) VALUES (99)")
    connection.commit()
    connection.close()
    with pytest.raises(SchemaVersionError):
        AnnotationDatabase(root, tmp_path / "source").initialize()
