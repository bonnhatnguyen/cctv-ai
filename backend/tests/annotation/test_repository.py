from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from app.v1.contracts import RunSummary, VideoMetadata
from app.annotation.contracts import (
    ActionAnnotationCreate,
    ActionAnnotationUpdate,
    ActionMutation,
    InteractionCreate,
    InteractionUpdate,
    MediaView,
    Point,
    RegisterClip,
    ReviewCoverageWrite,
    RoiWrite,
    TemplateWrite,
)
from app.annotation.database import AnnotationDatabase, DatabaseBindingError, SchemaVersionError
from app.annotation.repository import NotFound, PayloadConflict, RevisionConflict, SourceUnavailable
from app.annotation.settings import resolve_annotation_root


TRIANGLE = [Point(x=0.2, y=0.2), Point(x=0.6, y=0.2), Point(x=0.6, y=0.6)]


def prepare_action_workspace(repo, registered_clip, setup):
    clip = repo.save_roi(
        registered_clip.id,
        RoiWrite(
            operation_id=uuid4(),
            expected_clip_revision=registered_clip.revision,
            camera_setup_id=setup.id,
            polygon=TRIANGLE,
        ),
    )
    workspace = repo.create_interaction(
        clip.id,
        InteractionCreate(
            operation_id=uuid4(), expected_clip_revision=clip.revision, hand="right"
        ),
    )
    return clip, workspace, workspace.interactions[0]


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


def test_action_writes_are_versioned_idempotent_and_keep_overlaps(repo, registered_clip, setup):
    _, workspace, interaction = prepare_action_workspace(repo, registered_clip, setup)
    first_request = ActionAnnotationCreate(
        operation_id=uuid4(),
        expected_clip_revision=workspace.clip_revision,
        interaction_id=interaction.id,
        label="hand_in",
        start_frame=0,
        end_frame=2,
        crossing_frame=1,
        object_kind="unknown",
        visibility="clear",
    )
    first = repo.create_action(registered_clip.id, first_request)
    assert repo.create_action(registered_clip.id, first_request) == first
    second = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(),
            expected_clip_revision=first.clip_revision,
            interaction_id=interaction.id,
            label="take_out",
            start_frame=1,
            end_frame=2,
            crossing_frame=None,
            object_kind="cash",
            visibility="occluded",
        ),
    )
    assert [(item.label, item.start_frame, item.end_frame) for item in second.annotations] == [
        ("hand_in", 0, 2),
        ("take_out", 1, 2),
    ]
    with pytest.raises(RevisionConflict):
        repo.create_action(
            registered_clip.id,
            first_request.model_copy(update={"operation_id": uuid4()}),
        )


def test_action_lifecycle_keeps_tombstone_and_resets_review_on_edit(repo, registered_clip, setup):
    _, workspace, interaction = prepare_action_workspace(repo, registered_clip, setup)
    created = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(), expected_clip_revision=workspace.clip_revision,
            interaction_id=interaction.id, label="hand_out", start_frame=0,
            end_frame=2, crossing_frame=1, object_kind="cash", visibility="clear"
        ),
    )
    annotation = created.annotations[0]
    confirmed = repo.confirm_action(
        registered_clip.id,
        annotation.id,
        ActionMutation(
            operation_id=uuid4(), expected_clip_revision=created.clip_revision,
            expected_annotation_revision=annotation.revision,
        ),
    )
    assert confirmed.annotations[0].review_state == "confirmed"
    edited = repo.update_action(
        registered_clip.id,
        annotation.id,
        ActionAnnotationUpdate(
            operation_id=uuid4(), expected_clip_revision=confirmed.clip_revision,
            expected_annotation_revision=confirmed.annotations[0].revision,
            interaction_id=interaction.id, label="hand_out", start_frame=0,
            end_frame=1, crossing_frame=1, object_kind="cash", visibility="clear"
        ),
    )
    assert edited.annotations[0].review_state == "draft"
    deleted = repo.delete_action(
        registered_clip.id,
        annotation.id,
        ActionMutation(
            operation_id=uuid4(), expected_clip_revision=edited.clip_revision,
            expected_annotation_revision=edited.annotations[0].revision,
        ),
    )
    assert deleted.annotations[0].deleted is True
    restored = repo.restore_action(
        registered_clip.id,
        annotation.id,
        ActionMutation(
            operation_id=uuid4(), expected_clip_revision=deleted.clip_revision,
            expected_annotation_revision=deleted.annotations[0].revision,
        ),
    )
    assert restored.annotations[0].deleted is False
    assert restored.annotations[0].review_state == "draft"


def test_roi_change_invalidates_events_and_review_coverage(repo, registered_clip, setup):
    _, workspace, interaction = prepare_action_workspace(repo, registered_clip, setup)
    with_action = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(), expected_clip_revision=workspace.clip_revision,
            interaction_id=interaction.id, label="put_in", start_frame=0,
            end_frame=2, crossing_frame=None, object_kind="cash", visibility="clear"
        ),
    )
    covered = repo.create_review_coverage(
        registered_clip.id,
        ReviewCoverageWrite(
            operation_id=uuid4(), expected_clip_revision=with_action.clip_revision,
            start_frame=0, end_frame=2, reviewed_labels=["put_in"]
        ),
    )
    changed = repo.save_roi(
        registered_clip.id,
        RoiWrite(
            operation_id=uuid4(), expected_clip_revision=covered.clip_revision,
            camera_setup_id=setup.id,
            polygon=[Point(x=0.1, y=0.1), Point(x=0.8, y=0.1), Point(x=0.8, y=0.8)],
        ),
    )
    result = repo.get_action_workspace(changed.id)
    assert result.annotations[0].review_state == "needs_review"
    assert result.review_coverage[0].active is False
    assert result.annotations[0].roi_revision_id != result.roi_revision_id


def test_new_event_invalidates_only_its_interval_and_label_class(repo, registered_clip, setup):
    _, workspace, interaction = prepare_action_workspace(repo, registered_clip, setup)
    covered = repo.create_review_coverage(
        registered_clip.id,
        ReviewCoverageWrite(
            operation_id=uuid4(), expected_clip_revision=workspace.clip_revision,
            start_frame=0, end_frame=2, reviewed_labels=["hand_in", "hand_out"]
        ),
    )
    changed = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(), expected_clip_revision=covered.clip_revision,
            interaction_id=interaction.id, label="hand_in", start_frame=1,
            end_frame=1, crossing_frame=1, object_kind="unknown", visibility="clear"
        ),
    )
    active = [item for item in changed.review_coverage if item.active]
    assert [
        (item.start_frame, item.end_frame, set(item.reviewed_labels)) for item in active
    ] == [
        (0, 0, {"hand_in", "hand_out"}),
        (1, 1, {"hand_out"}),
        (2, 2, {"hand_in", "hand_out"}),
    ]


def test_interaction_edit_invalidates_confirmed_events_in_that_group(repo, registered_clip, setup):
    _, workspace, interaction = prepare_action_workspace(repo, registered_clip, setup)
    created = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(), expected_clip_revision=workspace.clip_revision,
            interaction_id=interaction.id, label="take_out", start_frame=0,
            end_frame=1, crossing_frame=None, object_kind="cash", visibility="clear"
        ),
    )
    event = created.annotations[0]
    confirmed = repo.confirm_action(
        registered_clip.id,
        event.id,
        ActionMutation(
            operation_id=uuid4(), expected_clip_revision=created.clip_revision,
            expected_annotation_revision=event.revision,
        ),
    )
    changed = repo.update_interaction(
        registered_clip.id,
        interaction.id,
        InteractionUpdate(
            operation_id=uuid4(), expected_clip_revision=confirmed.clip_revision,
            expected_interaction_revision=interaction.revision, hand="left"
        ),
    )
    assert changed.interactions[0].hand == "left"
    assert changed.annotations[0].review_state == "needs_review"
    assert changed.annotations[0].revision == 3


def test_unclear_cannot_overlap_confirmed_same_group_and_class(repo, registered_clip, setup):
    _, workspace, interaction = prepare_action_workspace(repo, registered_clip, setup)
    created = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(), expected_clip_revision=workspace.clip_revision,
            interaction_id=interaction.id, label="hand_in", start_frame=0,
            end_frame=2, crossing_frame=1, object_kind="unknown", visibility="clear"
        ),
    )
    event = created.annotations[0]
    confirmed = repo.confirm_action(
        registered_clip.id,
        event.id,
        ActionMutation(
            operation_id=uuid4(), expected_clip_revision=created.clip_revision,
            expected_annotation_revision=event.revision,
        ),
    )
    with pytest.raises(RevisionConflict, match=str(event.id)):
        repo.create_action(
            registered_clip.id,
            ActionAnnotationCreate(
                operation_id=uuid4(), expected_clip_revision=confirmed.clip_revision,
                interaction_id=interaction.id, label="unclear", start_frame=1,
                end_frame=2, crossing_frame=None, object_kind="unknown",
                visibility="occluded", uncertain_labels=["hand_in"],
                unclear_reason="occlusion",
            ),
        )


def test_workspace_remains_readable_when_source_becomes_unavailable(repo, registered_clip, setup):
    _, workspace, _ = prepare_action_workspace(repo, registered_clip, setup)
    repo.mark_source_state(registered_clip.id, "missing")
    readable = repo.get_action_workspace(registered_clip.id)
    assert readable.interactions == workspace.interactions
    with pytest.raises(SourceUnavailable):
        repo.create_interaction(
            registered_clip.id,
            InteractionCreate(
                operation_id=uuid4(), expected_clip_revision=readable.clip_revision,
                hand="unknown",
            ),
        )


def test_tracking_reference_requires_real_completed_evidence(repo, registered_clip, setup):
    clip = repo.save_roi(
        registered_clip.id,
        RoiWrite(
            operation_id=uuid4(), expected_clip_revision=registered_clip.revision,
            camera_setup_id=setup.id, polygon=TRIANGLE,
        ),
    )
    with pytest.raises(NotFound, match="evidence"):
        repo.create_interaction(
            clip.id,
            InteractionCreate(
                operation_id=uuid4(), expected_clip_revision=clip.revision,
                hand="right", tracking_job_id=clip.source_job_id, local_track_id=999,
            ),
        )


def test_tracking_reference_accepts_id_found_in_completed_evidence(
    repo, jobs, registered_clip, setup
):
    jobs.enqueue(str(registered_clip.source_job_id))
    private = jobs.mark_processing(str(registered_clip.source_job_id))
    assert private is not None
    output = Path(private.output_path)
    output.write_bytes(b"tracked-video")
    output.with_name("tracking.evidence.jsonl").write_text(
        '{"frame_index":0,"boxes":[{"track_id":7}]}\n', encoding="utf-8"
    )
    jobs.complete(
        str(registered_clip.source_job_id),
        output,
        RunSummary(
            actual_device="cpu", device_name="CPU", processed_frames=3,
            local_track_count=1, inference_samples=3, mean_inference_ms=1.0,
            tracking_wall_ms_total=3.0, processing_seconds=0.1,
            effective_fps=30.0, output_duration_ms=120,
        ),
    )
    clip = repo.save_roi(
        registered_clip.id,
        RoiWrite(
            operation_id=uuid4(), expected_clip_revision=registered_clip.revision,
            camera_setup_id=setup.id, polygon=TRIANGLE,
        ),
    )
    workspace = repo.create_interaction(
        clip.id,
        InteractionCreate(
            operation_id=uuid4(), expected_clip_revision=clip.revision, hand="right",
            tracking_job_id=clip.source_job_id, local_track_id=7,
        ),
    )
    assert workspace.interactions[0].local_track_id == 7


def test_operation_replay_fingerprint_includes_lifecycle_target(repo, registered_clip, setup):
    _, workspace, interaction = prepare_action_workspace(repo, registered_clip, setup)
    first = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(), expected_clip_revision=workspace.clip_revision,
            interaction_id=interaction.id, label="take_out", start_frame=0,
            end_frame=0, crossing_frame=None, object_kind="cash", visibility="clear"
        ),
    )
    second = repo.create_action(
        registered_clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(), expected_clip_revision=first.clip_revision,
            interaction_id=interaction.id, label="put_in", start_frame=2,
            end_frame=2, crossing_frame=None, object_kind="cash", visibility="clear"
        ),
    )
    first_event, second_event = second.annotations
    request = ActionMutation(
        operation_id=uuid4(), expected_clip_revision=second.clip_revision,
        expected_annotation_revision=first_event.revision,
    )
    repo.delete_action(registered_clip.id, first_event.id, request)
    with pytest.raises(PayloadConflict):
        repo.delete_action(registered_clip.id, second_event.id, request)


def test_action_rejects_interaction_owned_by_another_clip(
    repo, jobs, source_data_root, registered_clip, setup
):
    _, first_workspace, foreign_interaction = prepare_action_workspace(
        repo, registered_clip, setup
    )
    second_job_id = uuid4()
    source = source_data_root / "jobs" / str(second_job_id) / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"second-private-video")
    second_job = jobs.create_imported(
        str(second_job_id),
        "second.mp4",
        source,
        VideoMetadata(
            size_bytes=source.stat().st_size, width=640, height=360,
            duration_ms=120, fps_num=25, fps_den=1, frame_count_estimate=3,
            codec="h264", preview_supported=True,
        ),
    )
    second = repo.register_clip(
        RegisterClip(operation_id=uuid4(), source_job_id=second_job.id)
    )
    second = repo.complete_preparation(
        second.id,
        source_sha256="2" * 64,
        media=MediaView(
            frame_count=3, fps_num=25, fps_den=1, width=640, height=360,
            sample_aspect_ratio="1:1",
        ),
        artifact_manifest={"schema_version": 1, "chunks": []},
        prepared_bytes=123,
    )
    second = repo.save_roi(
        second.id,
        RoiWrite(
            operation_id=uuid4(), expected_clip_revision=second.revision,
            camera_setup_id=setup.id, polygon=TRIANGLE,
        ),
    )
    with pytest.raises(NotFound, match="does not belong"):
        repo.create_action(
            second.id,
            ActionAnnotationCreate(
                operation_id=uuid4(), expected_clip_revision=second.revision,
                interaction_id=foreign_interaction.id, label="hand_in",
                start_frame=0, end_frame=2, crossing_frame=1,
                object_kind="unknown", visibility="clear",
            ),
        )
    assert repo.get_action_workspace(registered_clip.id).clip_revision == first_workspace.clip_revision


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
