from __future__ import annotations

from uuid import uuid4

import pytest

from app.annotation.assistance_store import AssistanceRepository, AssistanceStateConflict
from app.annotation.contracts import AssistanceRunCreate, Point, RoiWrite
from app.annotation.contracts import ActionAnnotationCreate, InteractionCreate
from app.annotation.repository import PayloadConflict


@pytest.fixture
def ready_clip_with_roi(repo, registered_clip, setup):
    return repo.save_roi(
        registered_clip.id,
        RoiWrite(
            operation_id=uuid4(),
            expected_clip_revision=registered_clip.revision,
            camera_setup_id=setup.id,
            polygon=[
                Point(x=0.4, y=0.4),
                Point(x=0.6, y=0.4),
                Point(x=0.6, y=0.6),
                Point(x=0.4, y=0.6),
            ],
        ),
    )


@pytest.fixture
def store(repo):
    return AssistanceRepository(repo.database)


def _request(clip_revision: int, *, operation_id=None, start=0, end=2):
    return AssistanceRunCreate(
        operation_id=operation_id or uuid4(),
        expected_clip_revision=clip_revision,
        model="dino",
        start_frame=start,
        end_frame=end,
    )


def test_create_run_is_idempotent_and_freezes_binding(store, ready_clip_with_roi):
    request = _request(ready_clip_with_roi.revision)
    first = store.create_run(ready_clip_with_roi.id, request)
    second = store.create_run(ready_clip_with_roi.id, request)

    assert second.id == first.id
    assert first.source_sha256 == "1" * 64
    assert first.roi_revision_id == ready_clip_with_roi.roi.id
    assert first.guideline_version == 1
    assert first.status == "queued"


def test_reusing_operation_id_with_different_range_is_rejected(
    store, ready_clip_with_roi
):
    operation_id = uuid4()
    store.create_run(
        ready_clip_with_roi.id,
        _request(ready_clip_with_roi.revision, operation_id=operation_id),
    )
    with pytest.raises(PayloadConflict):
        store.create_run(
            ready_clip_with_roi.id,
            _request(
                ready_clip_with_roi.revision,
                operation_id=operation_id,
                end=1,
            ),
        )


def test_publish_requires_running_state_and_orders_suggestions(
    store, ready_clip_with_roi
):
    run = store.create_run(
        ready_clip_with_roi.id, _request(ready_clip_with_roi.revision)
    )
    proposals = [
        {
            "proposal_key": "later",
            "label": None,
            "action_start_frame": None,
            "action_end_frame": None,
            "view_start_frame": 2,
            "view_end_frame": 2,
            "crossing_estimate": None,
            "crossing_bracket_start": None,
            "crossing_bracket_end": None,
            "reason": "track_gap",
            "evidence": {"samples": [2]},
        },
        {
            "proposal_key": "earlier",
            "label": "hand_in",
            "action_start_frame": 0,
            "action_end_frame": 1,
            "view_start_frame": 0,
            "view_end_frame": 2,
            "crossing_estimate": 1,
            "crossing_bracket_start": 0,
            "crossing_bracket_end": 1,
            "reason": "crossing",
            "evidence": {"samples": [0, 1]},
        },
    ]

    with pytest.raises(AssistanceStateConflict):
        store.publish(run.id, store.run_binding(run.id), proposals)
    store.mark_running(run.id, scheduled_frames=3)
    completed = store.publish(run.id, store.run_binding(run.id), proposals)

    assert completed.status == "succeeded"
    items = store.list_suggestions(ready_clip_with_roi.id).items
    assert [item.proposal_key for item in items] == ["earlier", "later"]
    assert items[1].label is None


def test_annotation_only_revision_change_does_not_make_run_stale(
    store, repo, ready_clip_with_roi
):
    run = store.create_run(
        ready_clip_with_roi.id, _request(ready_clip_with_roi.revision)
    )
    with repo.database.write_transaction() as connection:
        connection.execute(
            "UPDATE annotation_clips SET revision=revision+1 WHERE id=?",
            (str(ready_clip_with_roi.id),),
        )
    store.mark_running(run.id, scheduled_frames=3)

    assert store.publish(run.id, store.run_binding(run.id), []).status == "succeeded"


def _pending_suggestion(store, clip):
    run = store.create_run(clip.id, _request(clip.revision))
    store.mark_running(run.id, scheduled_frames=3)
    store.publish(
        run.id,
        store.run_binding(run.id),
        [{
            "proposal_key": "proposal-accept",
            "label": "hand_in",
            "action_start_frame": 0,
            "action_end_frame": 2,
            "view_start_frame": 0,
            "view_end_frame": 2,
            "crossing_estimate": 1,
            "crossing_bracket_start": 0,
            "crossing_bracket_end": 1,
            "reason": "crossing",
            "evidence": {},
        }],
    )
    return store.list_suggestions(clip.id).items[0]


def test_create_action_claims_suggestion_in_the_same_transaction(
    store, repo, ready_clip_with_roi
):
    suggestion = _pending_suggestion(store, ready_clip_with_roi)
    workspace = repo.create_interaction(
        ready_clip_with_roi.id,
        InteractionCreate(
            operation_id=uuid4(),
            expected_clip_revision=ready_clip_with_roi.revision,
            hand="unknown",
        ),
    )
    result = repo.create_action(
        ready_clip_with_roi.id,
        ActionAnnotationCreate(
            operation_id=uuid4(),
            expected_clip_revision=workspace.clip_revision,
            suggestion_id=suggestion.id,
            interaction_id=workspace.interactions[0].id,
            label="hand_in",
            start_frame=0,
            end_frame=2,
            crossing_frame=1,
            object_kind="unknown",
            visibility="clear",
        ),
    )

    accepted = store.get_suggestion(suggestion.id)
    assert accepted.review_state == "accepted"
    assert accepted.accepted_annotation_id == result.annotations[0].id


def test_failed_action_insert_leaves_suggestion_pending(
    store, repo, ready_clip_with_roi
):
    suggestion = _pending_suggestion(store, ready_clip_with_roi)
    with pytest.raises(Exception):
        repo.create_action(
            ready_clip_with_roi.id,
            ActionAnnotationCreate(
                operation_id=uuid4(),
                expected_clip_revision=ready_clip_with_roi.revision,
                suggestion_id=suggestion.id,
                interaction_id=uuid4(),
                label="hand_in",
                start_frame=0,
                end_frame=2,
                crossing_frame=1,
                object_kind="unknown",
                visibility="clear",
            ),
        )
    assert store.get_suggestion(suggestion.id).review_state == "pending"


def test_update_contract_does_not_accept_suggestion_id():
    from pydantic import ValidationError
    from app.annotation.contracts import ActionAnnotationUpdate

    with pytest.raises(ValidationError):
        ActionAnnotationUpdate(
            operation_id=uuid4(), expected_clip_revision=1,
            expected_annotation_revision=1, suggestion_id=uuid4(),
            interaction_id=uuid4(), label="hand_in", start_frame=0,
            end_frame=2, crossing_frame=1, object_kind="unknown", visibility="clear",
        )


def test_changing_roi_stales_pending_suggestions_and_old_runs(
    store, repo, ready_clip_with_roi, setup
):
    suggestion = _pending_suggestion(store, ready_clip_with_roi)
    queued = store.create_run(
        ready_clip_with_roi.id, _request(ready_clip_with_roi.revision)
    )

    changed = repo.save_roi(
        ready_clip_with_roi.id,
        RoiWrite(
            operation_id=uuid4(), expected_clip_revision=ready_clip_with_roi.revision,
            camera_setup_id=setup.id,
            polygon=[Point(x=.3, y=.3), Point(x=.7, y=.3), Point(x=.7, y=.7)],
        ),
    )

    assert changed.roi.id != ready_clip_with_roi.roi.id
    assert store.get_suggestion(suggestion.id).review_state == "stale"
    invalidated = store.get_run(queued.id)
    assert invalidated.status == "failed"
    assert invalidated.error_code == "stale_binding"
