#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


CLEAR_LABELS = ("hand_in", "hand_out", "take_out", "put_in")
TARGETS = (*CLEAR_LABELS, "unclear", "no_action")


class PilotError(ValueError):
    pass


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise PilotError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PilotError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise PilotError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != 1 or manifest.get("guideline_version") != 1:
        raise PilotError("pilot requires schema_version=1 and guideline_version=1")
    if not isinstance(manifest.get("pilot_id"), str) or not manifest["pilot_id"]:
        raise PilotError("pilot_id is required")
    source_hash = manifest.get("source_sha256")
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
        raise PilotError("source_sha256 must be lowercase SHA-256")
    _timestamp(manifest.get("selected_at"), "selected_at")
    segments = manifest.get("segments")
    if not isinstance(segments, list) or len(segments) != 20:
        raise PilotError("pilot must contain exactly 20 preselected segments")
    ids: set[str] = set()
    counts = {target: 0 for target in TARGETS}
    pending: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise PilotError("each segment must be an object")
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id or segment_id in ids:
            raise PilotError("segment_id must be non-empty and unique")
        ids.add(segment_id)
        start, end = segment.get("start_frame"), segment.get("end_frame")
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise PilotError(f"invalid frame interval for {segment_id}")
        target = segment.get("target")
        if target not in TARGETS:
            raise PilotError(f"invalid target for {segment_id}")
        counts[target] += 1
        status = segment.get("status")
        if status not in {"SELECTED", "PENDING_DATA"}:
            raise PilotError(f"invalid status for {segment_id}")
        if status == "PENDING_DATA":
            pending.append(segment_id)
    missing = [target for target, count in counts.items() if count < 2]
    if missing:
        raise PilotError("pilot needs at least two target slots for: " + ", ".join(missing))
    return {"target_counts": counts, "pending_segments": sorted(pending)}


def _second_pass_schedule(manifest: dict[str, Any], completed: datetime) -> dict[str, Any]:
    seed = f'{manifest["pilot_id"]}:{manifest["source_sha256"]}:second'
    selected = [segment for segment in manifest["segments"] if segment["status"] == "SELECTED"]
    ordered = sorted(selected, key=lambda item: hashlib.sha256(f'{seed}:{item["segment_id"]}'.encode()).digest())
    schedule = {
        "schema_version": 1,
        "pilot_id": manifest["pilot_id"],
        "clip_id": manifest["clip_id"],
        "source_sha256": manifest["source_sha256"],
        "not_before": (completed + timedelta(hours=24)).isoformat(),
        "segments": [
            {key: item[key] for key in ("segment_id", "start_frame", "end_frame")}
            for item in ordered
        ],
    }
    schedule["schedule_sha256"] = hashlib.sha256(
        json.dumps(schedule, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return schedule


def build_blinded_second_pass(manifest: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    summary = validate_manifest(manifest)
    if summary["pending_segments"]:
        raise PilotError("cannot prepare second pass while pilot is PENDING_DATA")
    passes = {item.get("name"): item for item in manifest.get("passes", []) if isinstance(item, dict)}
    first = passes.get("first")
    if first is None:
        raise PilotError("first pass is missing")
    completed = _timestamp(first.get("completed_at"), "first.completed_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current - completed < timedelta(hours=24):
        raise PilotError("second pass must wait at least 24 hours")
    return _second_pass_schedule(manifest, completed)


def temporal_iou(first: dict[str, Any], second: dict[str, Any]) -> float:
    intersection = max(0, min(first["end_frame"], second["end_frame"]) - max(first["start_frame"], second["start_frame"]) + 1)
    union = (first["end_frame"] - first["start_frame"] + 1) + (second["end_frame"] - second["start_frame"] + 1) - intersection
    return intersection / union if union else 0.0


def _compatible(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return first.get("label") == second.get("label") and first.get("hand") == second.get("hand")


def maximum_iou_matches(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    event_key = lambda indexed: (indexed[1]["start_frame"], indexed[1]["end_frame"], indexed[1].get("label", ""), indexed[1].get("hand", ""), indexed[0])
    first_order = sorted(enumerate(first), key=event_key)
    second_order = sorted(enumerate(second), key=event_key)

    def tie_key(pairs: tuple[tuple[int, int, float], ...]) -> tuple[tuple[int, int, int, int], ...]:
        return tuple(
            (first[left]["start_frame"], first[left]["end_frame"], second[right]["start_frame"], second[right]["end_frame"])
            for left, right, _ in pairs
        )

    @lru_cache(maxsize=None)
    def solve(position: int, used: int) -> tuple[float, tuple[tuple[int, int, float], ...]]:
        if position == len(first_order):
            return 0.0, ()
        best_score, best_pairs = solve(position + 1, used)
        first_index, first_event = first_order[position]
        for ordered_other, (second_index, candidate) in enumerate(second_order):
            if used & (1 << ordered_other) or not _compatible(first_event, candidate):
                continue
            overlap = temporal_iou(first_event, candidate)
            if overlap <= 0:
                continue
            tail_score, tail_pairs = solve(position + 1, used | (1 << ordered_other))
            proposal = ((first_index, second_index, overlap),) + tail_pairs
            score = overlap + tail_score
            if score > best_score or (score == best_score and proposal and (not best_pairs or tie_key(proposal) < tie_key(best_pairs))):
                best_score, best_pairs = score, proposal
        return best_score, best_pairs

    return list(solve(0, 0)[1])


def _validate_event(event: dict[str, Any], segment: dict[str, Any]) -> None:
    if event.get("label") not in (*CLEAR_LABELS, "unclear"):
        raise PilotError(f'invalid event label in {segment["segment_id"]}')
    if event.get("hand") not in {"left", "right", "unknown"}:
        raise PilotError(f'invalid hand in {segment["segment_id"]}')
    start, end = event.get("start_frame"), event.get("end_frame")
    if type(start) is not int or type(end) is not int or not (segment["start_frame"] <= start <= end <= segment["end_frame"]):
        raise PilotError(f'event outside segment {segment["segment_id"]}')
    crossing = event.get("crossing_frame")
    if event["label"] in {"hand_in", "hand_out"}:
        if type(crossing) is not int or not start <= crossing <= end:
            raise PilotError(f'crossing frame required in {segment["segment_id"]}')
    elif crossing is not None:
        raise PilotError(f'crossing frame forbidden in {segment["segment_id"]}')
    uncertain = event.get("uncertain_labels", [])
    if event["label"] == "unclear" and (not uncertain or any(label not in CLEAR_LABELS for label in uncertain)):
        raise PilotError(f'unclear event needs uncertain_labels in {segment["segment_id"]}')


def _signature(item: dict[str, Any]) -> tuple[str, ...]:
    tokens: set[str] = set()
    for event in item["events"]:
        if event["label"] == "unclear":
            tokens.add("unclear:" + "+".join(sorted(set(event["uncertain_labels"]))))
        else:
            tokens.add(event["label"])
    if not tokens and set(item.get("reviewed_no_action_labels", [])) == set(CLEAR_LABELS):
        tokens.add("no_action")
    if not tokens:
        raise PilotError(f'segment {item.get("segment_id", "?")} is unknown, not no_action')
    return tuple(sorted(tokens))


def _pass_items(manifest: dict[str, Any], name: str) -> tuple[dict[str, dict[str, Any]], datetime, datetime, list[str], dict[str, Any]]:
    passes = [item for item in manifest.get("passes", []) if isinstance(item, dict) and item.get("name") == name]
    if len(passes) != 1:
        raise PilotError(f"pilot needs exactly one {name} pass")
    started = _timestamp(passes[0].get("started_at"), f"{name}.started_at")
    completed = _timestamp(passes[0].get("completed_at"), f"{name}.completed_at")
    if completed < started:
        raise PilotError(f"{name} pass cannot complete before it starts")
    items = passes[0].get("items")
    if not isinstance(items, list):
        raise PilotError(f"{name}.items must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        segment_id = item.get("segment_id") if isinstance(item, dict) else None
        if not isinstance(segment_id, str) or segment_id in indexed:
            raise PilotError(f"{name} pass has invalid or duplicate segment")
        if not isinstance(item.get("events"), list) or not isinstance(item.get("reviewed_no_action_labels", []), list):
            raise PilotError(f"{name} pass has invalid item")
        indexed[segment_id] = item
    return indexed, started, completed, [item["segment_id"] for item in items], passes[0]


def evaluate_pilot(manifest: dict[str, Any]) -> dict[str, Any]:
    selection = validate_manifest(manifest)
    if selection["pending_segments"]:
        return {"schema_version": 1, "verdict": "PENDING_DATA", **selection}
    segments = {item["segment_id"]: item for item in manifest["segments"]}
    first, first_started, first_completed, _, _ = _pass_items(manifest, "first")
    second, second_started, _, second_order, second_record = _pass_items(manifest, "second")
    selected_at = _timestamp(manifest.get("selected_at"), "selected_at")
    if selected_at > first_started:
        raise PilotError("segments must be selected before the first pass")
    expected = set(segments)
    if set(first) != expected or set(second) != expected:
        raise PilotError("each pass must contain every selected segment exactly once")
    if second_started - first_completed < timedelta(hours=24):
        raise PilotError("second pass must start at least 24 hours after the first pass completes")
    expected_schedule = _second_pass_schedule(manifest, first_completed)
    expected_order = [item["segment_id"] for item in expected_schedule["segments"]]
    if second_record.get("schedule_sha256") != expected_schedule["schedule_sha256"] or second_order != expected_order:
        raise PilotError("second pass results do not match the blinded schedule")

    agreed = 0
    clear_totals = [0, 0]
    clear_matched = [0, 0]
    crossing_errors: list[int] = []
    disagreements: list[str] = []
    actual_counts = [{target: 0 for target in TARGETS}, {target: 0 for target in TARGETS}]
    for segment_id, segment in segments.items():
        pair = (first[segment_id], second[segment_id])
        for item in pair:
            for event in item["events"]:
                _validate_event(event, segment)
        signatures = (_signature(pair[0]), _signature(pair[1]))
        for pass_index, signature in enumerate(signatures):
            for token in signature:
                category = "unclear" if token.startswith("unclear:") else token
                actual_counts[pass_index][category] += 1
        if signatures[0] == signatures[1]:
            agreed += 1
        else:
            disagreements.append(segment_id)
        clear = [[event for event in item["events"] if event["label"] != "unclear"] for item in pair]
        matches = maximum_iou_matches(clear[0], clear[1])
        clear_totals[0] += len(clear[0])
        clear_totals[1] += len(clear[1])
        for left, right, overlap in matches:
            if overlap >= 0.5:
                clear_matched[0] += 1
                clear_matched[1] += 1
                if clear[0][left]["label"] in {"hand_in", "hand_out"}:
                    crossing_errors.append(abs(clear[0][left]["crossing_frame"] - clear[1][right]["crossing_frame"]))
    rates = [clear_matched[index] / clear_totals[index] if clear_totals[index] else 0.0 for index in (0, 1)]
    crossing_rate = sum(error <= 2 for error in crossing_errors) / len(crossing_errors) if crossing_errors else None
    actual_missing = {
        name: [target for target, count in actual_counts[index].items() if count < 2]
        for index, name in enumerate(("first", "second"))
    }
    missing_actual = any(actual_missing.values())
    verdict = "PENDING_DATA" if missing_actual or crossing_rate is None else (
        "PASS" if agreed >= 18 and min(rates) >= 0.8 and crossing_rate >= 0.9 else "FAIL"
    )
    return {
        "schema_version": 1,
        "verdict": verdict,
        "target_counts": selection["target_counts"],
        "pending_segments": [],
        "actual_counts": {"first": actual_counts[0], "second": actual_counts[1]},
        "actual_missing": actual_missing,
        "label_set_agreement": {"agreed": agreed, "total": 20, "rate": agreed / 20},
        "clear_event_match_rate": {"first": rates[0], "second": rates[1]},
        "crossing_within_2_frames": {"pairs": len(crossing_errors), "rate": crossing_rate, "errors": crossing_errors},
        "disagreement_segments": disagreements,
    }


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotError(f"cannot read manifest: {path}") from exc
    if not isinstance(value, dict):
        raise PilotError("manifest root must be an object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as target:
        json.dump(value, target, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        target.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the private V2 M2 labeling pilot without uploading data.")
    parser.add_argument("command", choices=("validate", "prepare-second", "evaluate"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        manifest = _read(args.manifest.resolve())
        if args.command == "validate":
            result = {"schema_version": 1, "verdict": "PENDING_DATA" if validate_manifest(manifest)["pending_segments"] else "READY", **validate_manifest(manifest)}
        elif args.command == "prepare-second":
            result = build_blinded_second_pass(manifest)
        else:
            result = evaluate_pilot(manifest)
        if args.output:
            _write(args.output.resolve(), result)
        else:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (PilotError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
