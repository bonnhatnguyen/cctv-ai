from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from .contracts import FrameSpan, FrozenSegment, Observation, Proposal, RunConfig


def _cross(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _on_segment(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    *,
    epsilon: float = 1e-12,
) -> bool:
    if abs(_cross(a, b, point)) > epsilon:
        return False
    return (
        min(a[0], b[0]) - epsilon <= point[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= point[1] <= max(a[1], b[1]) + epsilon
    )


def point_in_roi(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    inside = False
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        if _on_segment(point, a, b):
            return True
        if (a[1] > point[1]) != (b[1] > point[1]):
            crossing_x = (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]
            if point[0] < crossing_x:
                inside = not inside
    return inside


def _point_segment_distance(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.dist(point, a)
    projection = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_squared
    projection = min(1.0, max(0.0, projection))
    closest = (a[0] + projection * dx, a[1] + projection * dy)
    return math.dist(point, closest)


def _boundary_distance(
    point: tuple[float, float], polygon: tuple[tuple[float, float], ...]
) -> float:
    return min(
        _point_segment_distance(point, polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    )


def _center(observation: Observation) -> tuple[float, float]:
    x1, y1, x2, y2 = observation.bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


@dataclass
class _Track:
    local_id: int
    samples: list[tuple[int, Observation]] = field(default_factory=list)
    terminal_reason: str | None = None


def _unique_best(
    distances: list[list[float]],
    row: int,
    threshold: float,
    margin: float,
) -> int | None:
    ranked = sorted((distance, column) for column, distance in enumerate(distances[row]))
    if not ranked or ranked[0][0] > threshold:
        return None
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < margin:
        return None
    return ranked[0][1]


def _associate(
    previous: list[Observation],
    current: list[Observation],
    config: RunConfig,
) -> dict[int, int]:
    if not previous or not current:
        return {}
    distances = [[math.dist(_center(a), _center(b)) for b in current] for a in previous]
    previous_best = {
        row: _unique_best(distances, row, config.association_distance, config.ambiguity_margin)
        for row in range(len(previous))
    }
    transposed = [list(column) for column in zip(*distances, strict=True)]
    current_best = {
        column: _unique_best(
            transposed,
            column,
            config.association_distance,
            config.ambiguity_margin,
        )
        for column in range(len(current))
    }
    return {
        row: column
        for row, column in previous_best.items()
        if column is not None and current_best.get(column) == row
    }


def _motion_span(
    samples: list[tuple[int, Observation]],
    source_index: int,
    destination_index: int,
    epsilon: float,
) -> tuple[FrameSpan | None, bool]:
    centers = [_center(observation) for _, observation in samples]
    direction = (
        centers[destination_index][0] - centers[source_index][0],
        centers[destination_index][1] - centers[source_index][1],
    )
    start = source_index
    found_start = False
    while start > 0:
        delta = (
            centers[start][0] - centers[start - 1][0],
            centers[start][1] - centers[start - 1][1],
        )
        if math.hypot(*delta) <= epsilon or delta[0] * direction[0] + delta[1] * direction[1] <= 0:
            found_start = True
            break
        start -= 1

    end = destination_index
    found_end = False
    while end < len(samples) - 1:
        delta = (
            centers[end + 1][0] - centers[end][0],
            centers[end + 1][1] - centers[end][1],
        )
        end += 1
        if math.hypot(*delta) <= epsilon or delta[0] * direction[0] + delta[1] * direction[1] <= 0:
            found_end = True
            break
    if not (found_start and found_end):
        return None, False
    return FrameSpan(start_frame=samples[start][0], end_frame=samples[end][0]), True


def _proposal_id(
    segment: FrozenSegment,
    config_hash: str,
    track_id: int,
    label: str | None,
    view_span: FrameSpan,
    reason: str,
    crossing: int | None,
) -> UUID:
    key = ":".join(
        (
            str(segment.selection.segment_id),
            config_hash,
            str(track_id),
            label or "null",
            str(view_span.start_frame),
            str(view_span.end_frame),
            reason,
            str(crossing),
        )
    )
    return uuid5(NAMESPACE_URL, key)


def _view_span(segment: FrozenSegment, action: FrameSpan, config: RunConfig) -> FrameSpan:
    context_frames = math.ceil(config.context_ms * segment.fps_num / (1000 * segment.fps_den))
    return FrameSpan(
        start_frame=max(segment.selection.span.start_frame, action.start_frame - context_frames),
        end_frame=min(segment.selection.span.end_frame, action.end_frame + context_frames),
    )


def build_proposals(
    segment: FrozenSegment,
    observations: list[tuple[int, list[Observation]]],
    config: RunConfig,
) -> list[Proposal]:
    tracks: list[_Track] = []
    active: list[_Track] = []
    next_id = 0
    previous_frame: int | None = None

    for frame_index, frame_observations in observations:
        if previous_frame is not None and frame_index <= previous_frame:
            raise ValueError("observation frames must be strictly increasing")
        for observation in frame_observations:
            if observation.frame_index != frame_index:
                raise ValueError("observation frame_index does not match its frame bucket")
        ordered = sorted(frame_observations, key=lambda item: item.bbox)
        expected_gap = previous_frame is not None and frame_index - previous_frame != config.stride
        if expected_gap or not ordered:
            for track in active:
                track.terminal_reason = "track_gap"
            active = []
        if not ordered:
            previous_frame = frame_index
            continue

        previous = [track.samples[-1][1] for track in active]
        matches = _associate(previous, ordered, config)
        matched_current = set(matches.values())
        if active and len(matches) < len(active):
            reason = "association"
            for index, track in enumerate(active):
                if index not in matches:
                    track.terminal_reason = reason
        new_active: list[_Track] = []
        for previous_index, current_index in sorted(matches.items()):
            track = active[previous_index]
            track.samples.append((frame_index, ordered[current_index]))
            new_active.append(track)
        for current_index, observation in enumerate(ordered):
            if current_index in matched_current:
                continue
            track = _Track(local_id=next_id, samples=[(frame_index, observation)])
            next_id += 1
            tracks.append(track)
            new_active.append(track)
        active = sorted(new_active, key=lambda item: item.local_id)
        previous_frame = frame_index

    config_json = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
    proposals: list[Proposal] = []
    for track in tracks:
        states: list[str] = []
        for _, observation in track.samples:
            center = _center(observation)
            if _boundary_distance(center, segment.polygon) <= config.boundary_epsilon:
                states.append("boundary")
            else:
                states.append("inside" if point_in_roi(center, segment.polygon) else "outside")

        strict_runs: list[tuple[str, list[int]]] = []
        current_strict_state: str | None = None
        for index, state in enumerate(states):
            if state == "boundary":
                current_strict_state = None
                continue
            if strict_runs and current_strict_state == state:
                strict_runs[-1][1].append(index)
            else:
                strict_runs.append((state, [index]))
            current_strict_state = state

        made_crossing = False
        for (source_state, source_run), (destination_state, destination_run) in zip(
            strict_runs, strict_runs[1:]
        ):
            if source_state == destination_state:
                continue
            if len(source_run) < config.min_side_samples or len(destination_run) < config.min_side_samples:
                continue
            source_index = source_run[-1]
            destination_index = destination_run[0]
            action_span, complete = _motion_span(
                track.samples,
                source_index,
                destination_index,
                config.motion_epsilon,
            )
            if not complete or action_span is None:
                continue
            view_span = _view_span(segment, action_span, config)
            label = "hand_in" if source_state == "outside" else "hand_out"
            crossing = track.samples[destination_index][0]
            bracket = FrameSpan(
                start_frame=track.samples[source_index][0],
                end_frame=crossing,
            )
            proposals.append(
                Proposal(
                    proposal_id=_proposal_id(
                        segment, config_hash, track.local_id, label, view_span, "crossing", crossing
                    ),
                    segment_id=segment.selection.segment_id,
                    local_track_id=track.local_id,
                    label=label,
                    action_span=action_span,
                    view_span=view_span,
                    crossing_estimate=crossing,
                    crossing_bracket=bracket,
                    reason="crossing",
                )
            )
            made_crossing = True

        review_reason: str | None = None
        if track.terminal_reason:
            review_reason = track.terminal_reason
        elif not made_crossing and (
            (
                track.samples[0][0] == segment.selection.span.start_frame
                and states[0] == "inside"
            )
            or (
                track.samples[-1][0] == segment.selection.span.end_frame
                and states[-1] == "inside"
            )
        ):
            review_reason = "clip_boundary"
        elif "boundary" in states and not made_crossing:
            review_reason = "boundary"
        if review_reason:
            evidence = FrameSpan(
                start_frame=track.samples[0][0],
                end_frame=track.samples[-1][0],
            )
            view_span = _view_span(segment, evidence, config)
            proposals.append(
                Proposal(
                    proposal_id=_proposal_id(
                        segment, config_hash, track.local_id, None, view_span, review_reason, None
                    ),
                    segment_id=segment.selection.segment_id,
                    local_track_id=track.local_id,
                    view_span=view_span,
                    reason=review_reason,
                )
            )

    return sorted(
        proposals,
        key=lambda proposal: (
            proposal.view_span.start_frame,
            proposal.crossing_estimate if proposal.crossing_estimate is not None else -1,
            str(proposal.proposal_id),
        ),
    )
