from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from fractions import Fraction
from uuid import UUID

from .contracts import EffortRecord, FrameSpan, FrozenManifest, FrozenSegment, Proposal, ReferenceEvent


def temporal_iou(a: FrameSpan, b: FrameSpan) -> Fraction:
    overlap = max(0, min(a.end_frame, b.end_frame) - max(a.start_frame, b.start_frame) + 1)
    if overlap == 0:
        return Fraction(0)
    union = (
        a.end_frame - a.start_frame + 1
        + b.end_frame - b.start_frame + 1
        - overlap
    )
    return Fraction(overlap, union)


@dataclass
class _FlowEdge:
    to: int
    reverse: int
    capacity: int
    cost: Fraction


def _maximum_weight(
    edges: list[tuple[int, int, Fraction]],
    reference_count: int,
    proposal_count: int,
) -> Fraction:
    source = reference_count + proposal_count
    sink = source + 1
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]

    def add_edge(start: int, end: int, capacity: int, cost: Fraction) -> None:
        forward = _FlowEdge(end, len(graph[end]), capacity, cost)
        reverse = _FlowEdge(start, len(graph[start]), 0, -cost)
        graph[start].append(forward)
        graph[end].append(reverse)

    for reference_index in range(reference_count):
        add_edge(source, reference_index, 1, Fraction(0))
    for proposal_index in range(proposal_count):
        add_edge(reference_count + proposal_index, sink, 1, Fraction(0))
    for reference_index, proposal_index, weight in edges:
        add_edge(reference_index, reference_count + proposal_index, 1, -weight)

    total = Fraction(0)
    while True:
        distance: list[Fraction | None] = [None] * len(graph)
        parent: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = Fraction(0)
        for _ in range(len(graph) - 1):
            changed = False
            for node, outgoing in enumerate(graph):
                if distance[node] is None:
                    continue
                for edge_index, edge in enumerate(outgoing):
                    if edge.capacity <= 0:
                        continue
                    candidate = distance[node] + edge.cost
                    if distance[edge.to] is None or candidate < distance[edge.to]:
                        distance[edge.to] = candidate
                        parent[edge.to] = (node, edge_index)
                        changed = True
            if not changed:
                break
        if distance[sink] is None or distance[sink] >= 0:
            break
        node = sink
        while node != source:
            previous, edge_index = parent[node]  # type: ignore[misc]
            edge = graph[previous][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse].capacity += 1
            node = previous
        total -= distance[sink]
    return total


def match_events(
    reference: list[ReferenceEvent], proposals: list[Proposal]
) -> list[tuple[UUID, UUID, Fraction]]:
    ordered_reference = sorted(
        reference,
        key=lambda event: (event.span.start_frame, event.span.end_frame, str(event.event_id)),
    )
    ordered_proposals = sorted(
        (proposal for proposal in proposals if proposal.label and proposal.action_span),
        key=lambda proposal: (
            proposal.action_span.start_frame,  # type: ignore[union-attr]
            proposal.action_span.end_frame,  # type: ignore[union-attr]
            str(proposal.proposal_id),
        ),
    )
    candidates: list[tuple[int, int, Fraction]] = []
    for reference_index, event in enumerate(ordered_reference):
        for proposal_index, proposal in enumerate(ordered_proposals):
            if event.label != proposal.label:
                continue
            overlap = temporal_iou(event.span, proposal.action_span)  # type: ignore[arg-type]
            if overlap >= Fraction(1, 2):
                candidates.append((reference_index, proposal_index, overlap))
    candidates.sort()
    target = _maximum_weight(candidates, len(ordered_reference), len(ordered_proposals))
    forced: list[tuple[int, int, Fraction]] = []
    excluded: set[tuple[int, int]] = set()
    used_reference: set[int] = set()
    used_proposals: set[int] = set()
    forced_weight = Fraction(0)
    for candidate in candidates:
        reference_index, proposal_index, weight = candidate
        if reference_index in used_reference or proposal_index in used_proposals:
            continue
        remaining = [
            edge
            for edge in candidates
            if (edge[0], edge[1]) not in excluded
            and edge != candidate
            and edge[0] not in used_reference | {reference_index}
            and edge[1] not in used_proposals | {proposal_index}
        ]
        achievable = forced_weight + weight + _maximum_weight(
            remaining,
            len(ordered_reference),
            len(ordered_proposals),
        )
        if achievable == target:
            forced.append(candidate)
            forced_weight += weight
            used_reference.add(reference_index)
            used_proposals.add(proposal_index)
        else:
            excluded.add((reference_index, proposal_index))
    return [
        (
            ordered_reference[reference_index].event_id,
            ordered_proposals[proposal_index].proposal_id,
            weight,
        )
        for reference_index, proposal_index, weight in forced
    ]


def _merge_spans(spans: tuple[FrameSpan, ...] | list[FrameSpan]) -> list[FrameSpan]:
    merged: list[FrameSpan] = []
    for span in sorted(spans, key=lambda item: (item.start_frame, item.end_frame)):
        if not merged or span.start_frame > merged[-1].end_frame + 1:
            merged.append(span)
        else:
            merged[-1] = FrameSpan(
                start_frame=merged[-1].start_frame,
                end_frame=max(merged[-1].end_frame, span.end_frame),
            )
    return merged


def _subtract_spans(base: list[FrameSpan], ignored: tuple[FrameSpan, ...]) -> list[FrameSpan]:
    result = base
    for cut in _merge_spans(ignored):
        updated: list[FrameSpan] = []
        for span in result:
            if cut.end_frame < span.start_frame or cut.start_frame > span.end_frame:
                updated.append(span)
                continue
            if span.start_frame < cut.start_frame:
                updated.append(FrameSpan(start_frame=span.start_frame, end_frame=cut.start_frame - 1))
            if cut.end_frame < span.end_frame:
                updated.append(FrameSpan(start_frame=cut.end_frame + 1, end_frame=span.end_frame))
        result = updated
    return result


def _valid_coverage(segment: FrozenSegment, label: str) -> list[FrameSpan]:
    return _subtract_spans(
        _merge_spans(segment.coverage.get(label, ())),
        segment.ignored.get(label, ()),
    )


def _contains(spans: list[FrameSpan], target: FrameSpan) -> bool:
    return any(
        span.start_frame <= target.start_frame and target.end_frame <= span.end_frame
        for span in spans
    )


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _evaluate_segments(
    segments: tuple[FrozenSegment, ...], proposals: list[Proposal]
) -> dict[str, object]:
    proposal_by_segment: dict[UUID, list[Proposal]] = {}
    for proposal in proposals:
        proposal_by_segment.setdefault(proposal.segment_id, []).append(proposal)
    classes: dict[str, dict[str, object]] = {}
    all_crossing_errors: list[int] = []
    all_start_errors: list[int] = []
    all_end_errors: list[int] = []
    localization_reference = 0
    localization_found = 0

    for label in ("hand_in", "hand_out"):
        true_positives = false_positives = false_negatives = 0
        evaluated_proposals = unscored_proposals = reference_count = 0
        reviewed_seconds = Fraction(0)
        for segment in segments:
            segment_proposals = proposal_by_segment.get(segment.selection.segment_id, [])
            references = [event for event in segment.events if event.label == label]
            labeled = [proposal for proposal in segment_proposals if proposal.label == label]
            pairs = match_events(references, labeled)
            matched_reference = {pair[0] for pair in pairs}
            matched_proposal = {pair[1] for pair in pairs}
            by_reference = {event.event_id: event for event in references}
            by_proposal = {proposal.proposal_id: proposal for proposal in labeled}
            true_positives += len(pairs)
            evaluated_proposals += len(pairs)
            reference_count += len(references)
            false_negatives += len(references) - len(matched_reference)
            for reference_id, proposal_id, _ in pairs:
                event = by_reference[reference_id]
                proposal = by_proposal[proposal_id]
                all_crossing_errors.append(abs(event.crossing_frame - proposal.crossing_estimate))  # type: ignore[arg-type]
                all_start_errors.append(abs(event.span.start_frame - proposal.action_span.start_frame))  # type: ignore[union-attr]
                all_end_errors.append(abs(event.span.end_frame - proposal.action_span.end_frame))  # type: ignore[union-attr]
            valid = _valid_coverage(segment, label)
            reviewed_frames = sum(span.end_frame - span.start_frame + 1 for span in valid)
            reviewed_seconds += Fraction(
                reviewed_frames * segment.fps_den,
                segment.fps_num,
            )
            for proposal in labeled:
                if proposal.proposal_id in matched_proposal:
                    continue
                if proposal.action_span and _contains(valid, proposal.action_span):
                    false_positives += 1
                    evaluated_proposals += 1
                else:
                    unscored_proposals += 1
        precision_denominator = true_positives + false_positives
        recall_denominator = true_positives + false_negatives
        classes[label] = {
            "reference_count": reference_count,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "evaluated_proposals": evaluated_proposals,
            "unscored_proposals": unscored_proposals,
            "precision": (
                true_positives / precision_denominator if precision_denominator else None
            ),
            "precision_scope": "confirmed_positives_plus_fully_covered_negatives",
            "recall": true_positives / recall_denominator if recall_denominator else None,
            "reviewed_seconds": float(reviewed_seconds),
            "quality_state": (
                "descriptive"
                if reference_count > 0 and reviewed_seconds > 0
                else "pending_reference_or_coverage"
            ),
            "false_positives_per_minute": (
                false_positives / (float(reviewed_seconds) / 60)
                if reviewed_seconds > 0
                else None
            ),
        }

    for segment in segments:
        segment_proposals = proposal_by_segment.get(segment.selection.segment_id, [])
        for event in segment.events:
            localization_reference += 1
            if any(
                proposal.view_span.start_frame
                <= event.crossing_frame
                <= proposal.view_span.end_frame
                for proposal in segment_proposals
            ):
                localization_found += 1

    return {
        "classes": classes,
        "localization": {
            "reference_count": localization_reference,
            "found_count": localization_found,
            "recall": (
                localization_found / localization_reference if localization_reference else None
            ),
        },
        "localization_errors": {
            "crossing_absolute": all_crossing_errors,
            "start_absolute": all_start_errors,
            "end_absolute": all_end_errors,
            "crossing_median": statistics.median(all_crossing_errors) if all_crossing_errors else None,
            "crossing_p95": _percentile(all_crossing_errors, 0.95),
            "start_median": statistics.median(all_start_errors) if all_start_errors else None,
            "start_p95": _percentile(all_start_errors, 0.95),
            "end_median": statistics.median(all_end_errors) if all_end_errors else None,
            "end_p95": _percentile(all_end_errors, 0.95),
        },
    }


def _effort_summary(effort: list[EffortRecord]) -> dict[str, object]:
    usable = [
        record
        for record in effort
        if record.completed and record.outside_review_completed
    ]
    operators = sorted({record.operator for record in usable})
    assisted_run_ids = {
        record.model_run_id for record in usable if record.mode == "assisted"
    }
    if len(assisted_run_ids) > 1:
        return {
            "savings_ratio": None,
            "paired_operators": 0,
            "pending_reason": "effort records contain more than one assisted model run",
        }
    paired: list[tuple[float, float]] = []
    for operator in operators:
        manual = [record.elapsed_seconds for record in usable if record.operator == operator and record.mode == "manual"]
        assisted_records = [record for record in usable if record.operator == operator and record.mode == "assisted"]
        run_ids = {record.model_run_id for record in assisted_records}
        if manual and assisted_records and len(run_ids) == 1:
            paired.append((sum(manual) / len(manual), sum(record.elapsed_seconds for record in assisted_records) / len(assisted_records)))
    if not paired:
        return {"savings_ratio": None, "paired_operators": 0, "pending_reason": "paired completed manual and assisted effort is unavailable"}
    manual_total = sum(item[0] for item in paired)
    assisted_total = sum(item[1] for item in paired)
    return {
        "savings_ratio": (manual_total - assisted_total) / manual_total,
        "paired_operators": len(paired),
        "pending_reason": None,
    }


def evaluate(
    manifest: FrozenManifest,
    proposals: list[Proposal],
    effort: list[EffortRecord],
) -> dict[str, object]:
    segment_ids = {segment.selection.segment_id for segment in manifest.segments}
    proposal_ids = [proposal.proposal_id for proposal in proposals]
    if len(proposal_ids) != len(set(proposal_ids)):
        raise ValueError("duplicate proposal_id in evaluation input")
    unknown = [proposal.segment_id for proposal in proposals if proposal.segment_id not in segment_ids]
    if unknown:
        raise ValueError("proposal references a segment outside the frozen manifest")
    unknown_effort = [record.segment_id for record in effort if record.segment_id not in segment_ids]
    if unknown_effort:
        raise ValueError("effort record references a segment outside the frozen manifest")
    segment_by_id = {
        segment.selection.segment_id: segment for segment in manifest.segments
    }
    for proposal in proposals:
        selection = segment_by_id[proposal.segment_id].selection.span
        if not (
            selection.start_frame <= proposal.view_span.start_frame
            and proposal.view_span.end_frame <= selection.end_frame
        ):
            raise ValueError("proposal view_span lies outside its frozen segment")
        if proposal.action_span and not (
            selection.start_frame <= proposal.action_span.start_frame
            and proposal.action_span.end_frame <= selection.end_frame
        ):
            raise ValueError("proposal action_span lies outside its frozen segment")
    aggregate = _evaluate_segments(manifest.segments, proposals)
    partitions: dict[str, object] = {}
    for partition in ("tuning", "evaluation", "exploratory"):
        selected = tuple(
            segment for segment in manifest.segments if segment.selection.partition == partition
        )
        selected_ids = {segment.selection.segment_id for segment in selected}
        partition_report = _evaluate_segments(
            selected,
            [proposal for proposal in proposals if proposal.segment_id in selected_ids],
        )
        partition_report["interpretation"] = (
            "pending_data"
            if not selected or manifest.reference_state != "ready"
            else "held_out" if partition == "evaluation"
            else "tuning_only" if partition == "tuning"
            else "descriptive_only"
        )
        partitions[partition] = partition_report
    return {
        "reference_state": manifest.reference_state,
        "scope": "descriptive_aggregate_not_held_out",
        "missing_scenarios": list(manifest.missing_scenarios),
        **aggregate,
        "partitions": partitions,
        "effort": _effort_summary(effort),
    }
