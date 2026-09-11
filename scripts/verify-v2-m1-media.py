#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from PIL import Image  # noqa: E402

from app.annotation.database import canonical_root, root_fingerprint  # noqa: E402
from app.annotation.verification import (  # noqa: E402
    VerificationConfig,
    VerificationError,
    build_schedule,
    distinct_track_ids,
    load_config,
    validate_benchmark,
)


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _exclusive_json(path: Path, value: Any) -> None:
    payload = _canonical_json(value)
    with path.open("xb") as target:
        target.write(payload)


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != 200:
                raise VerificationError(f"unexpected HTTP {response.status} for {url}")
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read local service response: {url}") from exc
    if not isinstance(payload, dict):
        raise VerificationError(f"local service returned non-object JSON: {url}")
    return payload


def _read_only_database(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise VerificationError(f"required private database is missing: {path.name}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _validate_bindings(config: VerificationConfig) -> tuple[dict[str, Any], dict[str, Any], Path]:
    annotation_health = _request_json(config.base_url + "/api/v2/annotations/health")
    v1_health = _request_json(config.base_url + "/api/v1/health")
    if annotation_health.get("ready") is not True or annotation_health.get("service") != "basket-annotation":
        raise VerificationError("annotation health is not ready")
    if annotation_health.get("instance_id") != v1_health.get("instance_id"):
        raise VerificationError("V1 and annotation health instance IDs differ")
    expected_fingerprint = root_fingerprint(config.data_dir)
    if annotation_health.get("data_root_fingerprint") != expected_fingerprint:
        raise VerificationError("annotation health data root fingerprint mismatch")
    with _read_only_database(config.annotation_dir / "annotations.db") as database:
        row = database.execute(
            "SELECT source_data_root, data_root_fingerprint FROM annotation_binding WHERE singleton=1"
        ).fetchone()
        if row is None or row["source_data_root"] != canonical_root(config.data_dir):
            raise VerificationError("annotation database source root binding mismatch")
        if row["data_root_fingerprint"] != expected_fingerprint:
            raise VerificationError("annotation database fingerprint mismatch")
    clip = _request_json(config.base_url + f"/api/v2/annotations/clips/{config.clip_id}")
    if clip.get("id") != str(config.clip_id) or clip.get("preparation_state") != "ready":
        raise VerificationError("configured annotation clip is not ready")
    if clip.get("source_state") != "available" or not isinstance(clip.get("media"), dict):
        raise VerificationError("configured annotation source is unavailable")
    source_job_id = clip.get("source_job_id")
    if not isinstance(source_job_id, str):
        raise VerificationError("annotation clip has no source job")
    with _read_only_database(config.data_dir / "jobs.db") as jobs:
        row = jobs.execute(
            "SELECT source_path FROM v1_tracking_jobs WHERE id=?", (source_job_id,)
        ).fetchone()
    if row is None:
        raise VerificationError("annotation source job is absent from V1 database")
    expected_source = (config.data_dir / "jobs" / source_job_id / "source.mp4").resolve()
    actual_source = Path(row["source_path"]).resolve()
    if actual_source != expected_source or not actual_source.is_file():
        raise VerificationError("annotation source path escaped its private UUID directory")
    source_hash = _sha256_file(actual_source)
    if clip.get("source_sha256") != source_hash:
        raise VerificationError("annotation source hash mismatch")
    return clip, v1_health, actual_source


def _prepare(config: VerificationConfig) -> None:
    if not config.browser_executable.is_file():
        raise VerificationError("configured browser executable is missing")
    clip, health, source = _validate_bindings(config)
    try:
        with urllib.request.urlopen(config.frontend_url + "/", timeout=10) as response:
            if response.status != 200:
                raise VerificationError("frontend is not reachable")
    except OSError as exc:
        raise VerificationError("frontend is not reachable") from exc
    media = clip["media"]
    schedule = build_schedule(media["frame_count"], clip["id"], clip["source_sha256"])
    schedule.update(
        {
            "run_id": str(config.run_id),
            "base_url": config.base_url,
            "frontend_url": config.frontend_url,
            "source_bytes": source.stat().st_size,
            "prepared_bytes": clip.get("prepared_bytes"),
            "sample_aspect_ratio": media["sample_aspect_ratio"],
            "fps_num": media["fps_num"],
            "fps_den": media["fps_den"],
            "width": media["width"],
            "height": media["height"],
            "service_instance_id": health.get("instance_id"),
        }
    )
    config.run_dir.mkdir(parents=True, exist_ok=False)
    _exclusive_json(config.run_dir / "schedule.json", schedule)
    print(config.run_dir / "schedule.json")


def _decode_rgb_hashes(source: Path, width: int, height: int, indices: set[int]) -> dict[int, str]:
    if not indices:
        return {}
    process = subprocess.Popen(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(source),
            "-map", "0:v:0", "-an", "-fps_mode", "passthrough", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None
    frame_bytes = width * height * 3
    wanted = set(indices)
    hashes: dict[int, str] = {}
    index = 0
    try:
        while wanted:
            frame = process.stdout.read(frame_bytes)
            if not frame:
                break
            if len(frame) != frame_bytes:
                raise VerificationError("independent decoder returned a partial frame")
            if index in wanted:
                hashes[index] = hashlib.sha256(frame).hexdigest()
                wanted.remove(index)
            index += 1
        process.stdout.close()
        stderr = process.stderr.read()
        returncode = process.wait(timeout=30)
    except BaseException:
        process.kill()
        process.wait(timeout=5)
        raise
    if returncode or wanted:
        raise VerificationError("independent source decoder failed: " + stderr[-512:].decode("utf-8", "replace"))
    return hashes


def _read_evidence(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError("tracking evidence is missing or invalid") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise VerificationError("tracking evidence rows must be objects")
    return rows


def _verify(config: VerificationConfig) -> None:
    schedule_path = config.run_dir / "schedule.json"
    benchmark_path = config.run_dir / "benchmark.json"
    verification_path = config.run_dir / "verification.json"
    if verification_path.exists():
        raise VerificationError("verification report already exists for this run_id")
    try:
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError("schedule or browser benchmark report is missing") from exc
    schedule_hash = _sha256_file(schedule_path)
    if benchmark.get("schedule_sha256") != schedule_hash:
        raise VerificationError("browser benchmark schedule hash mismatch")
    if benchmark.get("run_id") != str(config.run_id):
        raise VerificationError("browser benchmark run ID mismatch")
    clip, _, source = _validate_bindings(config)
    if schedule.get("clip_id") != clip["id"] or schedule.get("source_sha256") != clip["source_sha256"]:
        raise VerificationError("schedule belongs to another clip or source")
    metrics = validate_benchmark(schedule, benchmark)
    samples = benchmark["samples"]
    indices = {sample["requestedIndex"] for sample in samples}
    source_hashes = _decode_rgb_hashes(source, schedule["width"], schedule["height"], indices)
    mismatches = [
        sample["requestedIndex"]
        for sample in samples
        if source_hashes.get(sample["requestedIndex"]) != sample["displayed_rgb_sha256"]
    ]
    if mismatches:
        raise VerificationError(f"browser RGB pixels differ from source at frame {mismatches[0]}")
    job = _request_json(config.base_url + f"/api/v1/jobs/{config.tracking_job_id}")
    summary = job.get("summary")
    if job.get("status") != "ready" or not isinstance(summary, dict):
        raise VerificationError("configured tracking job is not complete")
    if not str(summary.get("actual_device", "")).startswith("cuda:") or "3060 Ti" not in str(summary.get("device_name", "")):
        raise VerificationError("tracking gate did not run on the RTX 3060 Ti")
    evidence_path = config.data_dir / "jobs" / str(config.tracking_job_id) / "tracking.evidence.jsonl"
    track_ids = distinct_track_ids(
        _read_evidence(evidence_path), processed_frames=int(summary.get("processed_frames", 0))
    )
    if summary.get("local_track_count") != len(track_ids):
        raise VerificationError("local_track_count does not match distinct evidence IDs")
    report = {
        "schema_version": 1,
        "run_id": str(config.run_id),
        "verdict": "PASS",
        "clip_id": clip["id"],
        "source_sha256": clip["source_sha256"],
        "data_root_fingerprint": root_fingerprint(config.data_dir),
        "schedule_sha256": schedule_hash,
        "frame_comparisons": len(samples),
        "frame_matches": True,
        "adjacent_p95_ms": metrics["adjacent_p95_ms"],
        "far_p95_ms": metrics["far_p95_ms"],
        "tracking_job_id": str(config.tracking_job_id),
        "tracking_actual_device": summary["actual_device"],
        "tracking_device_name": summary["device_name"],
        "distinct_track_ids": len(track_ids),
        "local_track_count": summary["local_track_count"],
        "failures": [],
    }
    temp = verification_path.with_suffix(".tmp")
    with temp.open("xb") as target:
        target.write(_canonical_json(report))
    temp.replace(verification_path)
    print(verification_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify private V2 M1 media and browser evidence")
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        (_prepare if args.command == "prepare" else _verify)(config)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
