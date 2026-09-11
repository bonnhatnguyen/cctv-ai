from __future__ import annotations

from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from app.annotation_benchmark.contracts import FrameSpan, SelectionItem
from app.annotation_benchmark.snapshot import SnapshotError, _open_read_only, read_snapshot
from app.annotation.contracts import ActionAnnotationCreate, ActionMutation, ReviewCoverageWrite


def test_snapshot_reads_current_roi_confirmed_event_and_active_coverage_without_writes(
    private_fixture,
) -> None:
    before = private_fixture.logical_dump()

    snapshot = read_snapshot(
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.selection,
    )

    assert private_fixture.logical_dump() == before
    assert snapshot.reference_state == "pending_data"
    assert len(snapshot.segments) == 1
    segment = snapshot.segments[0]
    assert segment.roi_revision_id == private_fixture.roi_id
    assert segment.source_sha256 == "db3f5e4b39afeae386f100afd8f7f35ccb69ec7b1daaf272c96df20841aba7ba"
    assert segment.events[0].event_id == private_fixture.action_id
    assert segment.events[0].span.start_frame == 10
    assert segment.coverage["hand_in"][0].end_frame == 30
    assert segment.sar_num == 2
    assert segment.sar_den == 1


def test_snapshot_rejects_annotation_database_bound_to_another_source_root(
    private_fixture, tmp_path: Path
) -> None:
    with pytest.raises(SnapshotError, match="different private source root"):
        read_snapshot(
            (tmp_path / "other-source").resolve(),
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_rejects_source_path_outside_owned_job_directory(private_fixture) -> None:
    jobs = __import__("sqlite3").connect(private_fixture.jobs_path)
    try:
        jobs.execute(
            "UPDATE v1_tracking_jobs SET source_path=? WHERE id=?",
            (str(private_fixture.source_root / "other.mp4"), str(private_fixture.source_job_id)),
        )
        jobs.commit()
    finally:
        jobs.close()

    with pytest.raises(SnapshotError, match="owned job directory"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_does_not_create_a_missing_database(tmp_path: Path) -> None:
    source_root = (tmp_path / "source").resolve()
    annotation_root = (source_root / "annotations").resolve()
    source_root.mkdir()

    with pytest.raises(SnapshotError, match="database is missing"):
        read_snapshot(source_root, annotation_root, [])

    assert not annotation_root.exists()


def test_snapshot_connection_enforces_sqlite_query_only(private_fixture) -> None:
    connection = _open_read_only(private_fixture.annotation_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO schema_migrations VALUES(99,'now')")
    finally:
        connection.close()


def test_snapshot_rejects_non_exploratory_selection_without_confirmed_provenance(
    private_fixture,
) -> None:
    original = private_fixture.selection[0]
    selection = [
        SelectionItem(
            **{
                **original.model_dump(),
                "partition": "tuning",
                "provenance_confirmed": False,
            }
        )
    ]
    with pytest.raises(SnapshotError, match="confirmed provenance"):
        read_snapshot(private_fixture.source_root, private_fixture.annotation_root, selection)


def test_snapshot_rejects_overlapping_segments_from_the_same_clip(private_fixture) -> None:
    original = private_fixture.selection[0]
    overlapping = SelectionItem(
        segment_id=uuid4(),
        clip_id=original.clip_id,
        span=FrameSpan(start_frame=20, end_frame=40),
        partition="exploratory",
        provenance_confirmed=False,
        scenario_tags=("exit",),
    )
    with pytest.raises(SnapshotError, match="overlap"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            [original, overlapping],
        )


def test_snapshot_rejects_tuning_evaluation_leakage_from_the_same_source(
    private_fixture,
) -> None:
    first = private_fixture.selection[0]
    tuning = SelectionItem(
        **{
            **first.model_dump(),
            "partition": "tuning",
            "provenance_confirmed": True,
            "parent_recording_id": "recording-a",
        }
    )
    evaluation = SelectionItem(
        segment_id=uuid4(),
        clip_id=first.clip_id,
        span=FrameSpan(start_frame=40, end_frame=60),
        partition="evaluation",
        parent_recording_id="recording-a",
        recording_days=("2026-09-10",),
        provenance_confirmed=True,
        scenario_tags=("exit",),
    )
    with pytest.raises(SnapshotError, match="partition leakage"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            [tuning, evaluation],
        )


def test_snapshot_rejects_future_annotation_schema(private_fixture) -> None:
    connection = sqlite3.connect(private_fixture.annotation_path)
    try:
        connection.execute("INSERT INTO schema_migrations VALUES(99,'future')")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SnapshotError, match="schema version"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_rejects_duplicate_segment_ids(private_fixture) -> None:
    first = private_fixture.selection[0]
    duplicate = SelectionItem(
        segment_id=first.segment_id,
        clip_id=first.clip_id,
        span=FrameSpan(start_frame=40, end_frame=50),
        partition="exploratory",
        provenance_confirmed=False,
        scenario_tags=("exit",),
    )
    with pytest.raises(SnapshotError, match="duplicate segment"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            [first, duplicate],
        )


def test_snapshot_rejects_source_hash_change(private_fixture) -> None:
    private_fixture.source_path.write_bytes(b"changed-private-video")
    with pytest.raises(SnapshotError, match="hash"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_rejects_media_metadata_disagreement_between_databases(
    private_fixture,
) -> None:
    jobs = sqlite3.connect(private_fixture.jobs_path)
    try:
        raw = jobs.execute(
            "SELECT metadata_json FROM v1_tracking_jobs WHERE id=?",
            (str(private_fixture.source_job_id),),
        ).fetchone()[0]
        payload = __import__("json").loads(raw)
        payload["width"] = 1280
        jobs.execute(
            "UPDATE v1_tracking_jobs SET metadata_json=? WHERE id=?",
            (__import__("json").dumps(payload), str(private_fixture.source_job_id)),
        )
        jobs.commit()
    finally:
        jobs.close()

    with pytest.raises(SnapshotError, match="metadata"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_rejects_missing_source_job(private_fixture) -> None:
    annotations = sqlite3.connect(private_fixture.annotation_path)
    try:
        annotations.execute(
            "UPDATE annotation_clips SET source_job_id=? WHERE id=?",
            (str(uuid4()), str(private_fixture.clip_id)),
        )
        annotations.commit()
    finally:
        annotations.close()

    with pytest.raises(SnapshotError, match="source job"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_rejects_missing_source_file(private_fixture) -> None:
    private_fixture.source_path.unlink()
    with pytest.raises(SnapshotError, match="unavailable"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_rejects_source_symlink_that_resolves_outside_job_directory(
    private_fixture, tmp_path: Path
) -> None:
    outside = (tmp_path / "outside.mp4").resolve()
    outside.write_bytes(private_fixture.source_path.read_bytes())
    private_fixture.source_path.unlink()
    try:
        private_fixture.source_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(SnapshotError, match="owned job directory"):
        read_snapshot(
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.selection,
        )


def test_snapshot_clips_partial_draft_to_ignored_instead_of_positive(private_fixture) -> None:
    connection = sqlite3.connect(private_fixture.annotation_path)
    try:
        connection.execute(
            "UPDATE action_annotations SET review_state='draft',start_frame=0,end_frame=40 "
            "WHERE id=?",
            (str(private_fixture.action_id),),
        )
        connection.commit()
    finally:
        connection.close()
    selected = private_fixture.selection[0]
    selection = [
        SelectionItem(
            **{
                **selected.model_dump(),
                "span": {"start_frame": 5, "end_frame": 30},
            }
        )
    ]

    snapshot = read_snapshot(
        private_fixture.source_root, private_fixture.annotation_root, selection
    )

    assert snapshot.segments[0].events == ()
    assert snapshot.segments[0].ignored["hand_in"] == (
        FrameSpan(start_frame=5, end_frame=30),
    )


def test_ready_requires_complete_coverage_for_both_hand_classes(private_fixture) -> None:
    repo = private_fixture.repository
    clip = repo.get_clip(private_fixture.clip_id)
    workspace = repo.create_action(
        clip.id,
        ActionAnnotationCreate(
            operation_id=uuid4(),
            expected_clip_revision=clip.revision,
            interaction_id=private_fixture.interaction_id,
            label="hand_out",
            start_frame=40,
            end_frame=50,
            crossing_frame=45,
            object_kind="unknown",
            visibility="clear",
        ),
    )
    hand_out = next(item for item in workspace.annotations if item.label == "hand_out")
    workspace = repo.confirm_action(
        clip.id,
        hand_out.id,
        ActionMutation(
            operation_id=uuid4(),
            expected_clip_revision=workspace.clip_revision,
            expected_annotation_revision=hand_out.revision,
        ),
    )
    all_scenarios = (
        "entry",
        "exit",
        "stationary",
        "near_pass",
        "boundary_jitter",
        "multiple_hands",
        "occlusion",
        "no_action",
    )
    selection = [
        SelectionItem(
            segment_id=uuid4(),
            clip_id=clip.id,
            span=FrameSpan(start_frame=0, end_frame=70),
            partition="exploratory",
            provenance_confirmed=False,
            scenario_tags=all_scenarios,
        )
    ]
    pending = read_snapshot(
        private_fixture.source_root, private_fixture.annotation_root, selection
    )
    assert pending.reference_state == "pending_data"

    repo.create_review_coverage(
        clip.id,
        ReviewCoverageWrite(
            operation_id=uuid4(),
            expected_clip_revision=workspace.clip_revision,
            start_frame=0,
            end_frame=70,
            reviewed_labels=["hand_in", "hand_out"],
        ),
    )
    ready = read_snapshot(
        private_fixture.source_root, private_fixture.annotation_root, selection
    )
    assert ready.reference_state == "ready"
