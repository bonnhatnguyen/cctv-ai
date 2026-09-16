from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Iterator, Literal
from uuid import UUID, uuid4

from app.inference_lease import InferenceLease
from app.owned_process import OwnedProcess

from .contracts import FrozenManifest, ModelAsset, Proposal, RunConfig
from .media import SourceDecodeError, SourceMediaChanged, iter_source_frames
from .models import load_detector
from .proposals import build_proposals
from .snapshot import SnapshotError, read_snapshot


class BenchmarkRunError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if not detail else f"{reason}: {detail}")


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_seconds(start_ns: int) -> float:
    measured = (time.perf_counter_ns() - start_ns) / 1_000_000_000
    return max(measured, time.get_clock_info("perf_counter").resolution)


def _ensure_owned_directory(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve(strict=True)
    candidate.mkdir(parents=True, exist_ok=True)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root):
        raise BenchmarkRunError("output_escape", str(candidate))
    return resolved


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    if path.exists() and path.is_symlink():
        raise BenchmarkRunError("output_escape", "benchmark lock must not be a symlink")
    if path.exists() and not path.resolve().is_relative_to(path.parent.resolve(strict=True)):
        raise BenchmarkRunError("output_escape", "benchmark lock escapes its root")
    handle: IO[bytes] = path.open("a+b")
    try:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BenchmarkRunError("benchmark_busy") from exc
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _validate_frozen(
    manifest: FrozenManifest, source_root: Path, annotation_root: Path
) -> None:
    try:
        current = read_snapshot(
            source_root,
            annotation_root,
            [segment.selection for segment in manifest.segments],
        )
    except SnapshotError as exc:
        raise BenchmarkRunError("stale_snapshot", str(exc)) from exc
    expected = [segment.model_dump(mode="json") for segment in manifest.segments]
    actual = [segment.model_dump(mode="json") for segment in current.segments]
    if (
        actual != expected
        or current.reference_state != manifest.reference_state
        or current.missing_scenarios != manifest.missing_scenarios
    ):
        raise BenchmarkRunError("stale_snapshot", "ROI, labels, coverage or source changed")


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _write_artifact_manifest(stage: Path) -> None:
    files = {
        item.relative_to(stage).as_posix(): _sha256_file(item)
        for item in sorted(stage.rglob("*"))
        if item.is_file() and item.name != "artifacts.sha256.json"
    }
    required = {
        "asset.json",
        "config.json",
        "manifest.json",
        "observations.jsonl",
        "progress.json",
        "proposals.json",
        "review-spans.md",
        "run.json",
        "status.json",
        "worker-result.json",
    }
    if not required.issubset(files):
        raise BenchmarkRunError("incomplete_artifacts")
    _write_json(stage / "artifacts.sha256.json", {"schema_version": 1, "files": files})


def _check_capacity(root: Path, config: RunConfig) -> None:
    if _directory_size(root) > config.output_quota_bytes:
        raise BenchmarkRunError("output_quota_exceeded")
    free = shutil.disk_usage(root).free
    if free - config.free_disk_reserve_bytes < 0:
        raise BenchmarkRunError("free_disk_reserve")


def _require_v1_idle(source_root: Path) -> None:
    database = (source_root / "jobs.db").resolve(strict=True)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        active = connection.execute(
            "SELECT 1 FROM v1_tracking_jobs WHERE status IN ('queued','processing') LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        raise BenchmarkRunError("v1_state_unavailable", str(exc)) from exc
    finally:
        connection.close()
    if active is not None:
        raise BenchmarkRunError("v1_busy", "wait until V1 tracking is idle")


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, BenchmarkRunError):
        return exc.reason
    if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
        return "out_of_memory"
    if isinstance(exc, SourceMediaChanged):
        return "stale_snapshot"
    if isinstance(exc, SourceDecodeError):
        return "decode_error"
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted"
    return "run_failed"


def _source_path(source_root: Path, source_job_id: UUID) -> Path:
    jobs = (source_root / "jobs").resolve(strict=True)
    source = (jobs / str(source_job_id) / "source.mp4").resolve(strict=True)
    if not source.is_relative_to(jobs) or not source.is_file():
        raise BenchmarkRunError("source_unavailable")
    return source


def _write_review_spans(
    path: Path, manifest: FrozenManifest, proposals: list[Proposal]
) -> None:
    lines = ["# Assisted-label review spans", ""]
    by_segment: dict[UUID, list[Proposal]] = {}
    for proposal in proposals:
        by_segment.setdefault(proposal.segment_id, []).append(proposal)
    for segment in manifest.segments:
        selection = segment.selection
        lines.extend(
            [
                f"## Clip `{selection.clip_id}` / segment `{selection.segment_id}`",
                "",
            ]
        )
        selected = by_segment.get(selection.segment_id, [])
        if not selected:
            lines.extend(
                [
                    "No model proposal spans. This does not mean no action; review the full "
                    f"selected interval `{selection.span.start_frame}-{selection.span.end_frame}`.",
                    "",
                ]
            )
            continue
        for proposal in selected:
            claimed = proposal.label or "review_only"
            lines.append(
                f"- Frames `{proposal.view_span.start_frame}-{proposal.view_span.end_frame}`: "
                f"`{claimed}` / reason `{proposal.reason}` / proposal `{proposal.proposal_id}`"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _synchronize_cuda() -> None:
    import torch

    torch.cuda.synchronize()


def _perform_inference(
    stage: Path,
    manifest: FrozenManifest,
    config: RunConfig,
    model_name: Literal["mediapipe", "dino"],
    asset: ModelAsset,
    source_root: Path,
    model_root: Path,
) -> dict[str, object]:
    load_started = time.perf_counter_ns()
    detector = load_detector(model_name, model_root, asset, config)
    model_load_seconds = _duration_seconds(load_started)
    proposals: list[Proposal] = []
    sampled_frames: list[dict[str, object]] = []
    decode_seconds = 0.0
    inference_seconds = 0.0
    postprocess_seconds = 0.0
    processed_frames = 0
    try:
        _write_json(
            stage / "progress.json",
            {
                "state": "loading_model",
                "processed_frames": 0,
                "segment_id": None,
                "segment_started_at": None,
            },
        )
        with (stage / "observations.jsonl").open("wb") as observation_stream:
            for segment in manifest.segments:
                segment_started_at = time.time()
                _write_json(
                    stage / "progress.json",
                    {
                        "state": "running",
                        "processed_frames": processed_frames,
                        "segment_id": str(segment.selection.segment_id),
                        "segment_started_at": segment_started_at,
                    },
                )
                source = _source_path(source_root, segment.source_job_id)
                buckets = []
                decode_started = time.perf_counter_ns()
                for frame in iter_source_frames(source, segment, config):
                    decode_seconds += _duration_seconds(decode_started)
                    if config.device.startswith("cuda"):
                        _synchronize_cuda()
                    inference_started = time.perf_counter_ns()
                    observations = detector.detect(frame)
                    if config.device.startswith("cuda"):
                        _synchronize_cuda()
                    inference_seconds += _duration_seconds(inference_started)
                    if any(item.frame_index != frame.source_index for item in observations):
                        raise BenchmarkRunError("invalid_observation_frame")
                    buckets.append((frame.source_index, observations))
                    record = {
                        "segment_id": str(segment.selection.segment_id),
                        "frame_index": frame.source_index,
                        "timestamp_ms": frame.timestamp_ms,
                        "transform": {
                            key: getattr(frame.transform, key)
                            for key in frame.transform.__dataclass_fields__
                        },
                        "observations": [item.model_dump(mode="json") for item in observations],
                    }
                    observation_stream.write(_canonical_bytes(record))
                    observation_stream.flush()
                    sampled_frames.append(
                        {"segment_id": record["segment_id"], "frame_index": frame.source_index}
                    )
                    processed_frames += 1
                    _write_json(
                        stage / "progress.json",
                        {
                            "state": "running",
                            "processed_frames": processed_frames,
                            "segment_id": str(segment.selection.segment_id),
                            "segment_started_at": segment_started_at,
                        },
                    )
                    _check_capacity(stage.parent.parent, config)
                    decode_started = time.perf_counter_ns()
                expected = len(
                    range(
                        segment.selection.span.start_frame,
                        segment.selection.span.end_frame + 1,
                        config.stride,
                    )
                )
                if len(buckets) != expected:
                    raise BenchmarkRunError(
                        "missing_scheduled_frames", f"expected {expected}, got {len(buckets)}"
                    )
                post_started = time.perf_counter_ns()
                proposals.extend(build_proposals(segment, buckets, config))
                postprocess_seconds += _duration_seconds(post_started)
        _write_json(stage / "proposals.json", [item.model_dump(mode="json") for item in proposals])
        _write_review_spans(stage / "review-spans.md", manifest, proposals)
        _write_json(
            stage / "progress.json",
            {
                "state": "completed",
                "processed_frames": processed_frames,
                "segment_id": None,
                "segment_started_at": None,
            },
        )
        return {
            "sampled_frames": sampled_frames,
            "model_load_seconds": model_load_seconds,
            "decode_seconds": decode_seconds,
            "inference_seconds": inference_seconds,
            "postprocess_seconds": postprocess_seconds,
            "peak_ram_bytes": None,
            "peak_gpu_bytes": None,
        }
    finally:
        detector.close()


def _execute_worker(
    stage: Path,
    manifest: FrozenManifest,
    config: RunConfig,
    model_name: Literal["mediapipe", "dino"],
    asset: ModelAsset,
    source_root: Path,
    model_root: Path,
) -> dict[str, object]:
    result_path = stage / "worker-result.json"
    command = [
        sys.executable,
        "-m",
        "app.annotation_benchmark.worker",
        "--stage",
        str(stage),
        "--source-root",
        str(source_root),
        "--model-root",
        str(model_root),
        "--model",
        model_name,
    ]
    environment = os.environ.copy()
    environment.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HTTP_PROXY="http://127.0.0.1:9",
        HTTPS_PROXY="http://127.0.0.1:9",
        NO_PROXY="",
    )
    with (stage / "worker.stdout.log").open("wb") as stdout, (
        stage / "worker.stderr.log"
    ).open("wb") as stderr, OwnedProcess(
        command, env=environment, stdout=stdout, stderr=stderr
    ) as process:
        try:
            progress_path = stage / "progress.json"
            started = time.monotonic()
            last_mtime_ns: int | None = None
            while process.poll() is None:
                now = time.monotonic()
                try:
                    mtime_ns = progress_path.stat().st_mtime_ns
                except OSError:
                    mtime_ns = None
                if mtime_ns is not None and mtime_ns != last_mtime_ns:
                    last_mtime_ns = mtime_ns
                if mtime_ns is None and now - started > config.segment_deadline_seconds:
                    raise BenchmarkRunError("model_load_deadline")
                if mtime_ns is not None:
                    try:
                        progress = json.loads(progress_path.read_text(encoding="utf-8"))
                        segment_started_at = progress.get("segment_started_at")
                    except (OSError, json.JSONDecodeError):
                        segment_started_at = None
                    if (
                        isinstance(segment_started_at, (int, float))
                        and time.time() - segment_started_at > config.segment_deadline_seconds
                    ):
                        raise BenchmarkRunError("deadline_exceeded")
                _check_capacity(stage.parent.parent, config)
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            return_code = process.returncode
        except BenchmarkRunError:
            raise
        except KeyboardInterrupt:
            raise
    if return_code != 0:
        error_path = stage / "worker-error.json"
        if error_path.is_file():
            error = json.loads(error_path.read_text(encoding="utf-8"))
            raise BenchmarkRunError(error.get("reason", "worker_failed"), error.get("detail"))
        raise BenchmarkRunError("worker_failed", f"exit_code={return_code}")
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkRunError("worker_result_missing") from exc


def run_benchmark(
    manifest: FrozenManifest,
    config: RunConfig,
    model_name: Literal["mediapipe", "dino"],
    asset: ModelAsset,
    source_root: Path,
    annotation_root: Path,
    model_root: Path,
) -> Path:
    source_root = source_root.resolve(strict=True)
    annotation_root = annotation_root.resolve(strict=True)
    if not annotation_root.is_relative_to(source_root):
        raise BenchmarkRunError("binding_mismatch")
    benchmark_root = _ensure_owned_directory(annotation_root, annotation_root / "benchmarks")
    staging_root = _ensure_owned_directory(annotation_root, benchmark_root / "staging")
    runs_root = _ensure_owned_directory(annotation_root, benchmark_root / "runs")
    failed_root = _ensure_owned_directory(annotation_root, benchmark_root / "failed")

    with _exclusive_lock(benchmark_root / ".run.lock"), InferenceLease().acquire():
        _check_capacity(benchmark_root, config)
        _require_v1_idle(source_root)
        _validate_frozen(manifest, source_root, annotation_root)
        run_id = uuid4()
        stage = staging_root / str(run_id)
        stage.mkdir(exist_ok=False)
        started = time.monotonic()
        status = {
            "schema_version": 1,
            "run_id": str(run_id),
            "state": "running",
            "reason": None,
            "started_at": _utc_now(),
            "finished_at": None,
        }
        _write_json(stage / "status.json", status)
        try:
            manifest_payload = manifest.model_dump(mode="json")
            config_payload = config.model_dump(mode="json")
            asset_payload = asset.model_dump(mode="json")
            _write_json(stage / "manifest.json", manifest_payload)
            _write_json(stage / "config.json", config_payload)
            _write_json(stage / "asset.json", asset_payload)
            worker_result = _execute_worker(
                stage,
                manifest,
                config,
                model_name,
                asset,
                source_root,
                model_root,
            )
            _write_json(stage / "worker-result.json", worker_result)
            _validate_frozen(manifest, source_root, annotation_root)
            elapsed = time.monotonic() - started
            run_payload = {
                "schema_version": 1,
                "run_id": str(run_id),
                "model": model_name,
                "input_hashes": {
                    "manifest": _sha256_bytes(_canonical_bytes(manifest_payload)),
                    "config": _sha256_bytes(_canonical_bytes(config_payload)),
                    "asset": _sha256_bytes(_canonical_bytes(asset_payload)),
                },
                "environment": {
                    "python": sys.version,
                    "platform": platform.platform(),
                    "device": config.device,
                },
                "binding": {
                    "source_root": str(source_root),
                    "annotation_root": str(annotation_root),
                },
                "sampled_frames": worker_result["sampled_frames"],
                "metrics": {
                    "model_load_seconds": worker_result["model_load_seconds"],
                    "decode_seconds": worker_result["decode_seconds"],
                    "inference_seconds": worker_result["inference_seconds"],
                    "postprocess_seconds": worker_result["postprocess_seconds"],
                    "end_to_end_seconds": elapsed,
                    "peak_ram_bytes": worker_result.get("peak_ram_bytes"),
                    "peak_gpu_bytes": worker_result.get("peak_gpu_bytes"),
                },
            }
            _write_json(stage / "run.json", run_payload)
            _check_capacity(benchmark_root, config)
            status.update(state="completed", finished_at=_utc_now())
            _write_json(stage / "status.json", status)
            _write_artifact_manifest(stage)
            destination = runs_root / str(run_id)
            if destination.exists():
                raise BenchmarkRunError("output_collision")
            stage.rename(destination)
            return destination
        except BaseException as exc:
            reason = _failure_reason(exc)
            status.update(state="failed", reason=reason, finished_at=_utc_now())
            try:
                _write_json(stage / "status.json", status)
                destination = failed_root / str(run_id)
                if not destination.exists():
                    stage.rename(destination)
            except OSError:
                pass
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise BenchmarkRunError(reason, str(exc)) from exc
