from __future__ import annotations

import ipaddress
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit
from uuid import UUID


class VerificationError(RuntimeError):
    """Raised when acceptance evidence is incomplete, inconsistent, or unsafe."""


@dataclass(frozen=True)
class VerificationConfig:
    schema_version: int
    base_url: str
    frontend_url: str
    data_dir: Path
    annotation_dir: Path
    clip_id: UUID
    tracking_job_id: UUID
    run_id: UUID
    browser_executable: Path

    @property
    def run_dir(self) -> Path:
        return self.annotation_dir / "acceptance" / str(self.run_id)


_CONFIG_FIELDS = {
    "schema_version",
    "base_url",
    "frontend_url",
    "data_dir",
    "annotation_dir",
    "clip_id",
    "tracking_job_id",
    "run_id",
    "browser_executable",
}


def _loopback_url(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise VerificationError(f"{name} must be a loopback HTTP URL")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VerificationError(f"{name} must be a loopback HTTP URL")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        if parsed.hostname != "localhost":
            raise VerificationError(f"{name} must be a loopback HTTP URL") from exc
    else:
        if not address.is_loopback:
            raise VerificationError(f"{name} must be a loopback HTTP URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise VerificationError(f"{name} must be a loopback HTTP URL") from exc
    if port is None or parsed.path not in ("", "/"):
        raise VerificationError(f"{name} must include only a loopback host and port")
    return value.rstrip("/")


def _absolute_path(value: object, name: str) -> Path:
    if not isinstance(value, str):
        raise VerificationError(f"{name} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise VerificationError(f"{name} must be an absolute path")
    return path.resolve()


def load_config(path: Path) -> VerificationConfig:
    if not path.is_absolute():
        raise VerificationError("config path must be absolute")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError("cannot read verification config") from exc
    if not isinstance(payload, dict) or set(payload) != _CONFIG_FIELDS:
        raise VerificationError("config fields do not match schema version 1")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise VerificationError("unsupported verification config schema")
    try:
        clip_id = UUID(payload["clip_id"])
        tracking_job_id = UUID(payload["tracking_job_id"])
        run_id = UUID(payload["run_id"])
    except (TypeError, ValueError, AttributeError) as exc:
        raise VerificationError("clip, tracking job, and run IDs must be UUIDs") from exc
    data_dir = _absolute_path(payload["data_dir"], "data_dir")
    annotation_dir = _absolute_path(payload["annotation_dir"], "annotation_dir")
    if annotation_dir != (data_dir / "annotations").resolve():
        raise VerificationError("annotation_dir must be data_dir/annotations")
    return VerificationConfig(
        schema_version=1,
        base_url=_loopback_url(payload["base_url"], "base_url"),
        frontend_url=_loopback_url(payload["frontend_url"], "frontend_url"),
        data_dir=data_dir,
        annotation_dir=annotation_dir,
        clip_id=clip_id,
        tracking_job_id=tracking_job_id,
        run_id=run_id,
        browser_executable=_absolute_path(payload["browser_executable"], "browser_executable"),
    )


def build_schedule(frame_count: int, clip_id: str, source_hash: str) -> dict[str, Any]:
    if isinstance(frame_count, bool) or frame_count < 52:
        raise VerificationError("benchmark clip must contain at least 52 frames")
    if len(source_hash) != 64:
        raise VerificationError("source hash must be SHA-256")
    middle = frame_count // 2
    adjacent = list(range(middle + 1, middle + 26)) + list(range(middle + 24, middle - 1, -1))
    order = [value for index in range(10) for value in (index, 19 - index)]
    far = [round(value * (frame_count - 1) / 19) for value in order]
    if min(adjacent) < 0 or max(adjacent) >= frame_count:
        raise VerificationError("clip is too short for adjacent benchmark schedule")
    return {
        "schema_version": 1,
        "clip_id": clip_id,
        "source_sha256": source_hash,
        "frame_count": frame_count,
        "initial_index": middle,
        "adjacent": adjacent,
        "far": far,
        "warm_up": [middle],
        "cache_policy": "service restart; OS filesystem cache uncontrolled",
    }


def p95(values: list[float]) -> float:
    if not values:
        raise VerificationError("cannot calculate p95 for an empty sample")
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def validate_benchmark(schedule: dict[str, Any], report: dict[str, Any]) -> dict[str, float]:
    if report.get("schema_version") != 1 or not isinstance(report.get("samples"), list):
        raise VerificationError("invalid benchmark report schema")
    failures = report.get("failures")
    if not isinstance(failures, list) or failures:
        raise VerificationError("benchmark reported failures")
    expected = [("adjacent", index) for index in schedule["adjacent"]] + [
        ("far", index) for index in schedule["far"]
    ]
    samples = report["samples"]
    if len(samples) != 70:
        raise VerificationError("benchmark must contain exactly 70 samples")
    adjacent_ms: list[float] = []
    far_ms: list[float] = []
    for sample, (kind, index) in zip(samples, expected, strict=True):
        if sample.get("kind") != kind or sample.get("requestedIndex") != index:
            raise VerificationError("benchmark sample order does not match schedule")
        if sample.get("displayedIndex") != index:
            raise VerificationError("browser displayed frame does not match requested frame")
        if sample.get("clipId") != schedule["clip_id"] or sample.get("sourceHash") != schedule["source_sha256"]:
            raise VerificationError("browser sample belongs to another clip or source")
        latency = sample.get("latencyMs")
        digest = sample.get("displayed_rgb_sha256")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or latency < 0:
            raise VerificationError("benchmark latency is invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise VerificationError("browser RGB hash is invalid")
        (adjacent_ms if kind == "adjacent" else far_ms).append(float(latency))
    adjacent_p95 = p95(adjacent_ms)
    far_p95 = p95(far_ms)
    if adjacent_p95 > 250:
        raise VerificationError("adjacent frame p95 exceeds 250 ms")
    if far_p95 > 2000:
        raise VerificationError("far frame p95 exceeds 2000 ms")
    return {"adjacent_p95_ms": adjacent_p95, "far_p95_ms": far_p95}


def distinct_track_ids(rows: Iterable[dict[str, Any]], *, processed_frames: int) -> set[int]:
    materialized = list(rows)
    if processed_frames > 0 and not materialized:
        raise VerificationError("empty evidence is invalid for a completed nonempty video")
    ids: set[int] = set()
    for row in materialized:
        boxes = row.get("boxes")
        if not isinstance(boxes, list):
            raise VerificationError("tracking evidence row has invalid boxes")
        for box in boxes:
            track_id = box.get("track_id") if isinstance(box, dict) else None
            if isinstance(track_id, bool) or not isinstance(track_id, int):
                raise VerificationError("tracking evidence has invalid track_id")
            ids.add(track_id)
    return ids
