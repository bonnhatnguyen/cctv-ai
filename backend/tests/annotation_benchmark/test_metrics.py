from __future__ import annotations

from fractions import Fraction
from itertools import permutations
from random import Random
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.annotation_benchmark.contracts import (
    EffortRecord,
    FrameSpan,
    FrozenManifest,
    FrozenSegment,
    Proposal,
    ReferenceEvent,
    SelectionItem,
)
from app.annotation_benchmark.metrics import evaluate, match_events, temporal_iou


def _event(label: str, start: int, end: int, crossing: int) -> ReferenceEvent:
    return ReferenceEvent(
        event_id=uuid4(),
        label=label,
        span=FrameSpan(start_frame=start, end_frame=end),
        crossing_frame=crossing,
        revision=1,
    )


def _proposal(
    segment_id,
    label: str | None,
    start: int,
    end: int,
    crossing: int | None = None,
) -> Proposal:
    span = FrameSpan(start_frame=start, end_frame=end)
    return Proposal(
        proposal_id=uuid4(),
        segment_id=segment_id,
        local_track_id=0,
        label=label,
        action_span=span if label else None,
        view_span=span,
        crossing_estimate=crossing if label else None,
        crossing_bracket=span if label else None,
        reason="crossing" if label else "boundary",
    )


def _manifest(
    *,
    events: tuple[ReferenceEvent, ...] = (),
    coverage: dict[str, tuple[FrameSpan, ...]] | None = None,
    ignored: dict[str, tuple[FrameSpan, ...]] | None = None,
) -> FrozenManifest:
    selection = SelectionItem(
        segment_id=uuid4(),
        clip_id=uuid4(),
        span=FrameSpan(start_frame=0, end_frame=99),
        partition="evaluation",
        parent_recording_id="recording-a",
        recording_days=("2026-09-10",),
        provenance_confirmed=True,
        scenario_tags=("entry",),
    )
    segment = FrozenSegment(
        selection=selection,
        source_job_id=uuid4(),
        source_sha256="0" * 64,
        clip_revision=1,
        roi_revision_id=uuid4(),
        polygon=((0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)),
        frame_count=100,
        width=640,
        height=360,
        fps_num=25,
        fps_den=1,
        sar_num=1,
        sar_den=1,
        events=events,
        coverage=coverage or {},
        ignored=ignored or {},
    )
    return FrozenManifest(
        manifest_id=uuid4(),
        frozen_at="2026-09-11T00:00:00Z",
        segments=(segment,),
        reference_state="pending_data",
        missing_scenarios=("exit",),
    )


def test_inclusive_iou_and_zero_overlap() -> None:
    assert temporal_iou(FrameSpan(start_frame=10, end_frame=20), FrameSpan(start_frame=20, end_frame=30)) == Fraction(1, 21)
    assert temporal_iou(FrameSpan(start_frame=10, end_frame=20), FrameSpan(start_frame=21, end_frame=30)) == 0


def test_matching_is_same_label_one_to_one_and_permutation_stable() -> None:
    refs = (_event("hand_in", 10, 20, 15), _event("hand_out", 10, 20, 15))
    segment_id = uuid4()
    best = _proposal(segment_id, "hand_in", 10, 20, 15)
    duplicate = _proposal(segment_id, "hand_in", 11, 20, 16)
    wrong_label = _proposal(segment_id, "hand_out", 30, 40, 35)

    forward = match_events(list(refs), [duplicate, wrong_label, best])
    reverse = match_events(list(reversed(refs)), [best, wrong_label, duplicate])

    assert forward == reverse
    assert forward == [(refs[0].event_id, best.proposal_id, Fraction(1, 1))]


def test_matching_total_iou_matches_exhaustive_oracle_for_small_cases() -> None:
    random = Random(20260911)
    for _ in range(30):
        segment_id = uuid4()
        refs = [
            _event("hand_in", start := random.randint(0, 15), start + random.randint(3, 10), start)
            for _ in range(4)
        ]
        proposals = []
        for _ in range(4):
            start = random.randint(0, 15)
            proposals.append(_proposal(segment_id, "hand_in", start, start + random.randint(3, 10), start))

        actual = sum((pair[2] for pair in match_events(refs, proposals)), Fraction(0))
        expected = Fraction(0)
        for ordering in permutations(range(len(proposals))):
            total = sum(
                (
                    overlap
                    for ref_index, proposal_index in enumerate(ordering)
                    if (overlap := temporal_iou(refs[ref_index].span, proposals[proposal_index].action_span)) >= Fraction(1, 2)
                ),
                Fraction(0),
            )
            expected = max(expected, total)
        assert actual == expected


def test_matching_tie_uses_canonical_proposal_id() -> None:
    reference = _event("hand_in", 10, 20, 15)
    segment_id = uuid4()
    later = _proposal(segment_id, "hand_in", 10, 20, 15).model_copy(
        update={"proposal_id": UUID(int=2)}
    )
    earlier = later.model_copy(update={"proposal_id": UUID(int=1)})

    assert match_events([reference], [later, earlier])[0][1] == UUID(int=1)


def test_matching_handles_many_sparse_events_without_exponential_matcher() -> None:
    segment_id = uuid4()
    refs = [_event("hand_in", index * 20, index * 20 + 5, index * 20 + 2) for index in range(60)]
    proposals = [_proposal(segment_id, "hand_in", index * 20, index * 20 + 5, index * 20 + 2) for index in range(60)]

    matches = match_events(list(reversed(refs)), list(reversed(proposals)))

    assert len(matches) == 60
    assert sum((match[2] for match in matches), Fraction(0)) == 60


def test_unreviewed_unmatched_proposal_is_not_false_positive() -> None:
    manifest = _manifest()
    segment_id = manifest.segments[0].selection.segment_id

    report = evaluate(manifest, [_proposal(segment_id, "hand_in", 10, 20, 15)], [])

    hand_in = report["classes"]["hand_in"]
    assert hand_in["precision"] is None
    assert hand_in["unscored_proposals"] == 1
    assert hand_in["false_positives"] == 0
    assert report["effort"]["savings_ratio"] is None


def test_fully_covered_unmatched_proposal_is_false_positive() -> None:
    manifest = _manifest(
        coverage={"hand_in": (FrameSpan(start_frame=0, end_frame=99),)}
    )
    segment_id = manifest.segments[0].selection.segment_id

    report = evaluate(manifest, [_proposal(segment_id, "hand_in", 10, 20, 15)], [])

    hand_in = report["classes"]["hand_in"]
    assert hand_in["precision"] == 0.0
    assert hand_in["false_positives"] == 1
    assert hand_in["evaluated_proposals"] == 1


def test_matched_proposal_counts_as_evaluated_and_reports_errors() -> None:
    event = _event("hand_in", 10, 20, 15)
    manifest = _manifest(events=(event,))
    segment_id = manifest.segments[0].selection.segment_id
    proposal = _proposal(segment_id, "hand_in", 11, 20, 16)

    report = evaluate(manifest, [proposal], [])

    assert report["classes"]["hand_in"]["evaluated_proposals"] == 1
    assert report["localization_errors"]["crossing_absolute"] == [1]
    assert report["localization_errors"]["start_absolute"] == [1]


def test_ignored_overlap_keeps_unmatched_proposal_unscored() -> None:
    manifest = _manifest(
        coverage={"hand_in": (FrameSpan(start_frame=0, end_frame=99),)},
        ignored={"hand_in": (FrameSpan(start_frame=15, end_frame=16),)},
    )
    segment_id = manifest.segments[0].selection.segment_id

    report = evaluate(manifest, [_proposal(segment_id, "hand_in", 10, 20, 15)], [])

    assert report["classes"]["hand_in"]["unscored_proposals"] == 1


def test_overlapping_coverage_uses_union_duration() -> None:
    manifest = _manifest(
        coverage={
            "hand_in": (
                FrameSpan(start_frame=0, end_frame=10),
                FrameSpan(start_frame=5, end_frame=20),
            )
        }
    )

    report = evaluate(manifest, [], [])

    assert report["classes"]["hand_in"]["reviewed_seconds"] == pytest.approx(21 / 25)


def test_localization_recall_uses_view_span_even_when_label_is_null() -> None:
    event = _event("hand_in", 10, 20, 15)
    manifest = _manifest(events=(event,))
    segment_id = manifest.segments[0].selection.segment_id

    report = evaluate(manifest, [_proposal(segment_id, None, 12, 18)], [])

    assert report["localization"]["reference_count"] == 1
    assert report["localization"]["found_count"] == 1
    assert report["localization"]["recall"] == 1.0
    assert report["classes"]["hand_in"]["false_negatives"] == 1


def test_low_action_iou_can_still_localize_reference_crossing() -> None:
    event = _event("hand_in", 10, 20, 15)
    manifest = _manifest(events=(event,))
    segment_id = manifest.segments[0].selection.segment_id
    proposal = _proposal(segment_id, "hand_in", 14, 15, 15).model_copy(
        update={"view_span": FrameSpan(start_frame=5, end_frame=25)}
    )

    report = evaluate(manifest, [proposal], [])

    assert report["localization"]["recall"] == 1.0
    assert report["classes"]["hand_in"]["recall"] == 0.0


def test_duplicate_proposal_id_and_unknown_effort_segment_are_rejected() -> None:
    manifest = _manifest()
    segment_id = manifest.segments[0].selection.segment_id
    proposal = _proposal(segment_id, "hand_in", 10, 20, 15)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(manifest, [proposal, proposal], [])

    effort = EffortRecord(
        segment_id=uuid4(), operator="a", mode="manual", order=1,
        elapsed_seconds=10, outside_proposal_review_seconds=0,
        outside_review_completed=True, completed=True,
    )
    with pytest.raises(ValueError, match="effort"):
        evaluate(manifest, [], [effort])


def test_effort_contract_rejects_assisted_without_run_and_invalid_subset() -> None:
    with pytest.raises(ValidationError, match="model_run_id"):
        EffortRecord(
            segment_id=uuid4(),
            operator="operator-a",
            mode="assisted",
            order=1,
            elapsed_seconds=10,
            outside_proposal_review_seconds=2,
            outside_review_completed=True,
            completed=True,
        )


def test_effort_does_not_mix_two_assisted_model_runs() -> None:
    manifest = _manifest()
    segment_id = manifest.segments[0].selection.segment_id
    records = [
        EffortRecord(segment_id=segment_id, operator="a", mode="manual", order=1, elapsed_seconds=20, outside_proposal_review_seconds=0, outside_review_completed=True, completed=True),
        EffortRecord(segment_id=segment_id, operator="a", mode="assisted", model_run_id=uuid4(), order=2, elapsed_seconds=10, outside_proposal_review_seconds=2, outside_review_completed=True, completed=True),
        EffortRecord(segment_id=segment_id, operator="b", mode="manual", order=2, elapsed_seconds=20, outside_proposal_review_seconds=0, outside_review_completed=True, completed=True),
        EffortRecord(segment_id=segment_id, operator="b", mode="assisted", model_run_id=uuid4(), order=1, elapsed_seconds=10, outside_proposal_review_seconds=2, outside_review_completed=True, completed=True),
    ]

    report = evaluate(manifest, [], records)

    assert report["effort"]["savings_ratio"] is None
    assert "model run" in report["effort"]["pending_reason"]


def test_paired_completed_effort_reports_measured_savings() -> None:
    manifest = _manifest()
    segment_id = manifest.segments[0].selection.segment_id
    run_id = uuid4()
    records = [
        EffortRecord(segment_id=segment_id, operator="a", mode="manual", order=1, elapsed_seconds=20, outside_proposal_review_seconds=0, outside_review_completed=True, completed=True),
        EffortRecord(segment_id=segment_id, operator="a", mode="assisted", model_run_id=run_id, order=2, elapsed_seconds=15, outside_proposal_review_seconds=5, outside_review_completed=True, completed=True),
    ]

    report = evaluate(manifest, [], records)

    assert report["effort"]["savings_ratio"] == 0.25
    assert report["effort"]["paired_operators"] == 1
    with pytest.raises(ValidationError, match="outside"):
        EffortRecord(
            segment_id=uuid4(),
            operator="operator-a",
            mode="manual",
            order=1,
            elapsed_seconds=10,
            outside_proposal_review_seconds=11,
            outside_review_completed=True,
            completed=True,
        )
