from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify-v2-m2-pilot.py"
SPEC = importlib.util.spec_from_file_location("v2_m2_pilot", SCRIPT)
assert SPEC and SPEC.loader
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


TARGETS = (["hand_in", "hand_out", "take_out", "put_in", "unclear", "no_action"] * 4)[:20]


def manifest(*, pending: set[str] | None = None):
    pending = pending or set()
    selected_at = datetime(2026, 9, 9, tzinfo=timezone.utc)
    segments = [
        {
            "segment_id": f"segment-{index:02d}",
            "start_frame": index * 20,
            "end_frame": index * 20 + 19,
            "target": target,
            "status": "PENDING_DATA" if f"segment-{index:02d}" in pending else "SELECTED",
        }
        for index, target in enumerate(TARGETS)
    ]
    first_items = [_pass_item(segment) for segment in segments if segment["status"] == "SELECTED"]
    data = {
        "schema_version": 1,
        "guideline_version": 1,
        "pilot_id": "shop-pilot-01",
        "clip_id": "11111111-1111-4111-8111-111111111111",
        "source_sha256": "a" * 64,
        "selected_at": selected_at.isoformat(),
        "segments": segments,
        "passes": [
            {
                "name": "first",
                "started_at": (selected_at + timedelta(hours=1)).isoformat(),
                "completed_at": (selected_at + timedelta(hours=2)).isoformat(),
                "items": first_items,
            },
        ],
    }
    if not pending:
        schedule = pilot.build_blinded_second_pass(data, now=selected_at + timedelta(hours=26))
        segment_by_id = {segment["segment_id"]: segment for segment in segments}
        data["passes"].append({
            "name": "second",
            "started_at": (selected_at + timedelta(hours=26)).isoformat(),
            "completed_at": (selected_at + timedelta(hours=27)).isoformat(),
            "schedule_sha256": schedule["schedule_sha256"],
            "items": [_pass_item(segment_by_id[item["segment_id"]]) for item in schedule["segments"]],
        })
    return data


def _pass_item(segment):
    target = segment["target"]
    base = {"segment_id": segment["segment_id"], "events": [], "reviewed_no_action_labels": []}
    if target == "no_action":
        base["reviewed_no_action_labels"] = ["hand_in", "hand_out", "take_out", "put_in"]
        return base
    event = {
        "label": target,
        "hand": "unknown" if target == "unclear" else "left",
        "start_frame": segment["start_frame"] + 2,
        "end_frame": segment["start_frame"] + 8,
        "crossing_frame": segment["start_frame"] + 5 if target in {"hand_in", "hand_out"} else None,
        "uncertain_labels": ["hand_in"] if target == "unclear" else [],
    }
    base["events"] = [event]
    return base


def test_selection_is_exactly_twenty_preselected_segments_with_required_examples():
    result = pilot.validate_manifest(manifest())
    assert result["pending_segments"] == []
    assert result["target_counts"]["hand_in"] >= 2
    broken = manifest()
    broken["segments"].pop()
    with pytest.raises(pilot.PilotError, match="exactly 20"):
        pilot.validate_manifest(broken)


def test_pending_footage_is_reported_instead_of_relabelled():
    data = manifest(pending={"segment-00"})
    result = pilot.evaluate_pilot(data)
    assert result["verdict"] == "PENDING_DATA"
    assert result["pending_segments"] == ["segment-00"]


def test_blinded_second_pass_is_deterministic_label_free_and_waits_24_hours():
    data = manifest()
    early = datetime(2026, 9, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(pilot.PilotError, match="24 hours"):
        pilot.build_blinded_second_pass(data, now=early)
    ready = datetime(2026, 9, 10, 2, tzinfo=timezone.utc)
    one = pilot.build_blinded_second_pass(data, now=ready)
    two = pilot.build_blinded_second_pass(data, now=ready)
    assert one == two
    assert all(set(item) == {"segment_id", "start_frame", "end_frame"} for item in one["segments"])
    assert [item["segment_id"] for item in one["segments"]] != [item["segment_id"] for item in data["segments"]]


def test_perfect_two_pass_pilot_passes_exact_metrics():
    result = pilot.evaluate_pilot(manifest())
    assert result["verdict"] == "PASS"
    assert result["label_set_agreement"] == {"agreed": 20, "total": 20, "rate": 1.0}
    assert result["clear_event_match_rate"]["first"] == 1.0
    assert result["clear_event_match_rate"]["second"] == 1.0
    assert result["crossing_within_2_frames"]["rate"] == 1.0


def test_temporal_iou_is_inclusive_and_matching_is_one_to_one_maximum():
    assert pilot.temporal_iou({"start_frame": 0, "end_frame": 0}, {"start_frame": 0, "end_frame": 0}) == 1.0
    first = [
        {"label": "take_out", "hand": "left", "start_frame": 0, "end_frame": 9, "crossing_frame": None, "uncertain_labels": []},
        {"label": "take_out", "hand": "left", "start_frame": 10, "end_frame": 19, "crossing_frame": None, "uncertain_labels": []},
    ]
    second = [
        {"label": "take_out", "hand": "left", "start_frame": 0, "end_frame": 14, "crossing_frame": None, "uncertain_labels": []},
        {"label": "take_out", "hand": "left", "start_frame": 15, "end_frame": 19, "crossing_frame": None, "uncertain_labels": []},
    ]
    matches = pilot.maximum_iou_matches(first, second)
    assert {(a, b) for a, b, _ in matches} == {(0, 0), (1, 1)}


def test_unreviewed_empty_segment_is_unknown_not_no_action():
    data = manifest()
    no_action_id = next(segment["segment_id"] for segment in data["segments"] if segment["target"] == "no_action")
    item = next(item for item in data["passes"][0]["items"] if item["segment_id"] == no_action_id)
    item["reviewed_no_action_labels"] = []
    with pytest.raises(pilot.PilotError, match="unknown, not no_action"):
        pilot.evaluate_pilot(data)


def test_declared_targets_cannot_hide_missing_actual_classes():
    data = manifest()
    for pass_record in data["passes"]:
        for item in pass_record["items"]:
            segment = next(value for value in data["segments"] if value["segment_id"] == item["segment_id"])
            item["reviewed_no_action_labels"] = []
            item["events"] = [{
                "label": "hand_in", "hand": "left",
                "start_frame": segment["start_frame"] + 2,
                "end_frame": segment["start_frame"] + 8,
                "crossing_frame": segment["start_frame"] + 5,
                "uncertain_labels": [],
            }]
    result = pilot.evaluate_pilot(data)
    assert result["verdict"] == "PENDING_DATA"
    assert "hand_out" in result["actual_missing"]["first"]
    assert "no_action" in result["actual_missing"]["second"]


def test_preselection_must_happen_before_the_first_pass():
    data = manifest()
    data["selected_at"] = "2026-09-12T00:00:00+00:00"
    with pytest.raises(pilot.PilotError, match="selected before"):
        pilot.evaluate_pilot(data)


def test_second_pass_start_and_blinded_schedule_are_verified():
    data = manifest()
    data["passes"][1]["started_at"] = "2026-09-09T02:01:00+00:00"
    data["passes"][1]["completed_at"] = "2026-09-10T03:00:00+00:00"
    with pytest.raises(pilot.PilotError, match="start at least 24 hours"):
        pilot.evaluate_pilot(data)

    data = manifest()
    data["passes"][1]["items"].reverse()
    with pytest.raises(pilot.PilotError, match="blinded schedule"):
        pilot.evaluate_pilot(data)


def test_matching_tie_prefers_earlier_start_then_end_not_input_order():
    first = [{"label": "take_out", "hand": "left", "start_frame": 0, "end_frame": 9}]
    second = [
        {"label": "take_out", "hand": "left", "start_frame": 5, "end_frame": 9},
        {"label": "take_out", "hand": "left", "start_frame": 0, "end_frame": 4},
    ]
    assert pilot.maximum_iou_matches(first, second)[0][:2] == (0, 1)

    first = [
        {"label": "hand_in", "hand": "left", "start_frame": 0, "end_frame": 4},
        {"label": "hand_in", "hand": "left", "start_frame": 5, "end_frame": 9},
    ]
    second = [{"label": "hand_in", "hand": "left", "start_frame": 0, "end_frame": 9}]
    assert pilot.maximum_iou_matches(first, second)[0][:2] == (0, 0)
