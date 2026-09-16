from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.annotation.verification import (
    VerificationError,
    build_schedule,
    distinct_track_ids,
    load_config,
    validate_benchmark,
)


def _config(tmp_path: Path) -> Path:
    data = (tmp_path / "data").resolve()
    annotation = (data / "annotations").resolve()
    data.mkdir()
    annotation.mkdir()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_url": "http://127.0.0.1:18001",
                "frontend_url": "http://localhost:18002",
                "data_dir": str(data),
                "annotation_dir": str(annotation),
                "clip_id": str(uuid4()),
                "tracking_job_id": str(uuid4()),
                "run_id": str(uuid4()),
                "browser_executable": str((tmp_path / "chrome.exe").resolve()),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_config_requires_loopback_absolute_roots_and_exact_schema(tmp_path: Path):
    path = _config(tmp_path)
    config = load_config(path.resolve())
    assert config.annotation_dir == config.data_dir / "annotations"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["base_url"] = "https://example.com"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VerificationError, match="loopback"):
        load_config(path.resolve())

    payload["base_url"] = "http://127.0.0.1:not-a-port"
    payload.pop("unexpected", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VerificationError, match="loopback"):
        load_config(path.resolve())

    payload["base_url"] = "http://127.0.0.1:18001"
    payload["unexpected"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VerificationError, match="fields"):
        load_config(path.resolve())


def test_schedule_is_deterministic_and_has_required_samples():
    schedule = build_schedule(751, "clip", "a" * 64)
    assert schedule["initial_index"] == 375
    assert len(schedule["adjacent"]) == 50
    assert len(schedule["far"]) == 20
    assert schedule == build_schedule(751, "clip", "a" * 64)


def test_benchmark_rejects_missing_samples_and_wrong_displayed_frame():
    schedule = build_schedule(80, "clip", "a" * 64)
    samples = []
    for kind in ("adjacent", "far"):
        for index in schedule[kind]:
            samples.append(
                {
                    "kind": kind,
                    "requestedIndex": index,
                    "displayedIndex": index,
                    "clipId": "clip",
                    "sourceHash": "a" * 64,
                    "generation": 1,
                    "inputAtMs": 1.0,
                    "paintedAtMs": 2.0,
                    "latencyMs": 1.0,
                    "displayed_rgb_sha256": "b" * 64,
                }
            )
    validate_benchmark(schedule, {"schema_version": 1, "samples": samples, "failures": []})
    with pytest.raises(VerificationError, match="70 samples"):
        validate_benchmark(schedule, {"schema_version": 1, "samples": samples[:-1], "failures": []})
    samples[0]["displayedIndex"] += 1
    with pytest.raises(VerificationError, match="displayed frame"):
        validate_benchmark(schedule, {"schema_version": 1, "samples": samples, "failures": []})


def test_tracking_id_cardinality_rejects_empty_completed_evidence():
    rows = [{"frame_index": 0, "boxes": [{"track_id": 4}]},
            {"frame_index": 1, "boxes": [{"track_id": 4}, {"track_id": 9}]},
            {"frame_index": 2, "boxes": []},
            {"frame_index": 3, "boxes": [{"track_id": 9}]}]
    assert distinct_track_ids(rows, processed_frames=4) == {4, 9}
    with pytest.raises(VerificationError, match="empty evidence"):
        distinct_track_ids([], processed_frames=4)
