from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from app.annotation_benchmark.contracts import (
    FrameSpan,
    FrozenSegment,
    Observation,
    ReferenceEvent,
    RunConfig,
    SelectionItem,
)
from app.annotation_benchmark.proposals import build_proposals, point_in_roi


def _segment() -> FrozenSegment:
    return FrozenSegment(
        selection=SelectionItem(
            segment_id=uuid4(),
            clip_id=uuid4(),
            span=FrameSpan(start_frame=0, end_frame=99),
            partition="exploratory",
            provenance_confirmed=False,
            scenario_tags=("entry",),
        ),
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
    )


def _box(frame: int, x: float, y: float = 0.5) -> Observation:
    half = 0.01
    return Observation(
        frame_index=frame,
        bbox=(x - half, y - half, x + half, y + half),
        score=None,
        score_kind="unavailable",
    )


def _path(points: list[tuple[int, float]]) -> list[tuple[int, list[Observation]]]:
    return [(frame, [_box(frame, x)]) for frame, x in points]


def test_boundary_is_inside_but_outside_is_not() -> None:
    roi = ((0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6))
    assert point_in_roi((0.4, 0.5), roi)
    assert point_in_roi((0.4, 0.4), roi)
    assert not point_in_roi((0.39, 0.5), roi)


def test_outside_to_inside_proposes_hand_in_with_evidence_bracket() -> None:
    segment = _segment()
    observations = _path([(9, 0.34), (10, 0.34), (11, 0.37), (12, 0.43), (13, 0.46), (14, 0.46)])

    proposals = build_proposals(segment, observations, RunConfig())

    crossings = [proposal for proposal in proposals if proposal.label == "hand_in"]
    assert len(crossings) == 1
    proposal = crossings[0]
    assert proposal.crossing_estimate == 12
    assert proposal.crossing_bracket == FrameSpan(start_frame=11, end_frame=12)
    assert proposal.action_span == FrameSpan(start_frame=10, end_frame=14)
    assert proposal.view_span == FrameSpan(start_frame=0, end_frame=39)


def test_inside_to_outside_proposes_hand_out() -> None:
    segment = _segment()
    observations = _path([(9, 0.46), (10, 0.46), (11, 0.43), (12, 0.37), (13, 0.34), (14, 0.34)])

    proposals = build_proposals(segment, observations, RunConfig())

    assert [proposal.label for proposal in proposals if proposal.label] == ["hand_out"]


def test_same_track_can_keep_overlapping_in_and_out_review_windows() -> None:
    segment = _segment()
    observations = _path(
        [
            (5, 0.34),
            (6, 0.34),
            (7, 0.37),
            (8, 0.43),
            (9, 0.46),
            (10, 0.46),
            (11, 0.43),
            (12, 0.37),
            (13, 0.34),
            (14, 0.34),
        ]
    )

    proposals = build_proposals(segment, observations, RunConfig())
    crossings = [proposal for proposal in proposals if proposal.label]

    assert [proposal.label for proposal in crossings] == ["hand_in", "hand_out"]
    assert crossings[0].view_span.end_frame >= crossings[1].view_span.start_frame
    assert crossings[0].proposal_id != crossings[1].proposal_id


def test_stationary_and_near_pass_do_not_create_crossing() -> None:
    segment = _segment()
    stationary = _path([(10, 0.5), (11, 0.5), (12, 0.5), (13, 0.5)])
    near_pass = _path([(20, 0.3), (21, 0.36), (22, 0.39), (23, 0.36), (24, 0.3)])

    proposals = build_proposals(segment, stationary + near_pass, RunConfig())

    assert not any(proposal.label is not None for proposal in proposals)


def test_hand_reappearing_inside_has_no_direction() -> None:
    segment = _segment()
    observations = [
        (10, [_box(10, 0.37)]),
        (11, [_box(11, 0.38)]),
        (12, []),
        (13, [_box(13, 0.43)]),
        (14, [_box(14, 0.45)]),
    ]

    proposals = build_proposals(segment, observations, RunConfig())

    assert not any(proposal.label in {"hand_in", "hand_out"} for proposal in proposals)
    assert any(proposal.reason == "track_gap" for proposal in proposals)


def test_boundary_jitter_is_review_only() -> None:
    segment = _segment()
    observations = _path([(10, 0.397), (11, 0.399), (12, 0.401), (13, 0.399)])

    proposals = build_proposals(
        segment,
        observations,
        RunConfig(boundary_epsilon=0.005),
    )

    assert any(proposal.reason == "boundary" and proposal.label is None for proposal in proposals)
    assert not any(proposal.label for proposal in proposals)


def test_boundary_sample_breaks_consecutive_side_debounce() -> None:
    segment = _segment()
    observations = _path(
        [(9, 0.37), (10, 0.37), (11, 0.4), (12, 0.37), (13, 0.43), (14, 0.43)]
    )

    proposals = build_proposals(
        segment,
        observations,
        RunConfig(boundary_epsilon=0.002),
    )

    assert not any(proposal.label for proposal in proposals)


def test_track_that_starts_inside_at_clip_boundary_is_review_only() -> None:
    segment = _segment()
    observations = _path([(0, 0.43), (1, 0.46), (2, 0.46)])

    proposals = build_proposals(segment, observations, RunConfig())

    assert any(proposal.reason == "clip_boundary" and proposal.label is None for proposal in proposals)


def test_ambiguous_two_hand_association_never_invents_crossing() -> None:
    segment = _segment()
    observations = [
        (10, [_box(10, 0.30), _box(10, 0.70)]),
        (11, [_box(11, 0.49), _box(11, 0.51)]),
    ]

    proposals = build_proposals(
        segment,
        observations,
        RunConfig(association_distance=0.5, ambiguity_margin=0.03),
    )

    assert any(proposal.reason == "association" for proposal in proposals)
    assert not any(proposal.label for proposal in proposals)


def test_detection_order_and_reference_data_do_not_change_proposals() -> None:
    segment = _segment()
    path = [(9, 0.34), (10, 0.34), (11, 0.37), (12, 0.43), (13, 0.46), (14, 0.46)]
    observations = [
        (frame, [_box(frame, 0.8), _box(frame, x)])
        for frame, x in path
    ]
    reversed_observations = [(frame, list(reversed(boxes))) for frame, boxes in observations]
    with_reference = segment.model_copy(
        update={
            "events": (
                ReferenceEvent(
                    event_id=uuid4(),
                    label="hand_out",
                    span=FrameSpan(start_frame=1, end_frame=2),
                    crossing_frame=1,
                    revision=1,
                ),
            ),
            "coverage": {"hand_out": (FrameSpan(start_frame=0, end_frame=99),)},
        }
    )

    original = build_proposals(segment, observations, RunConfig())
    permuted = build_proposals(segment, reversed_observations, RunConfig())
    leaked = build_proposals(with_reference, deepcopy(observations), RunConfig())

    assert original == permuted == leaked
