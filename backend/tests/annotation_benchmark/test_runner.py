from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest

import app.annotation_benchmark.runner as runner_module
from app.annotation_benchmark.contracts import ModelAsset, Observation, RunConfig
from app.annotation_benchmark.cli import main as cli_main
from app.annotation_benchmark.media import CropTransform, SampledFrame
from app.annotation_benchmark.runner import BenchmarkRunError, run_benchmark
from app.annotation_benchmark.snapshot import read_snapshot


class _Detector:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.closed = False

    def detect(self, frame: SampledFrame) -> list[Observation]:
        if self.error:
            raise self.error
        return []

    def close(self) -> None:
        self.closed = True


def _asset() -> ModelAsset:
    return ModelAsset(
        model_id="mediapipe-hand-landmarker",
        revision="1",
        files_sha256={"hand_landmarker.task": "0" * 64},
        license_source="local-test",
        license_sha256="1" * 64,
        telemetry_policy="allowed_by_user",
    )


def _frames(segment, config):
    transform = CropTransform(0, 0, 1, 1, 1, 1, segment.width, segment.height)
    for index in range(
        segment.selection.span.start_frame,
        segment.selection.span.end_frame + 1,
        config.stride,
    ):
        yield SampledFrame(index, index * 40, np.zeros((1, 1, 3), dtype=np.uint8), transform)


def _use_inline_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module, "_execute_worker", runner_module._perform_inference
    )


def test_completed_run_is_immutable_and_does_not_mutate_databases(
    private_fixture, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    manifest = read_snapshot(
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.selection,
    )
    detector = _Detector()
    before = private_fixture.logical_dump()
    monkeypatch.setattr("app.annotation_benchmark.runner.load_detector", lambda *args: detector)
    monkeypatch.setattr(
        "app.annotation_benchmark.runner.iter_source_frames",
        lambda source, segment, config, **kwargs: _frames(segment, config),
    )
    _use_inline_worker(monkeypatch)

    output = run_benchmark(
        manifest,
        RunConfig(output_quota_bytes=1024 * 1024, free_disk_reserve_bytes=0),
        "mediapipe",
        _asset(),
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.source_root,
    )

    assert output.parent == private_fixture.annotation_root / "benchmarks" / "runs"
    assert json.loads((output / "status.json").read_text())["state"] == "completed"
    observations = (output / "observations.jsonl").read_text().splitlines()
    assert len(observations) == 31
    assert all(json.loads(line)["observations"] == [] for line in observations)
    assert json.loads((output / "proposals.json").read_text()) == []
    review = (output / "review-spans.md").read_text()
    assert str(private_fixture.clip_id) in review
    assert "No model proposal spans" in review
    assert (output / "manifest.json").is_file()
    run = json.loads((output / "run.json").read_text())
    assert run["metrics"]["model_load_seconds"] >= 0
    assert run["metrics"]["postprocess_seconds"] > 0
    progress = json.loads((output / "progress.json").read_text())
    assert progress["state"] == "completed"
    assert progress["processed_frames"] == 31
    assert not list((output.parent.parent / "staging").glob("*"))
    assert detector.closed
    assert private_fixture.logical_dump() == before
    assert cli_main(["evaluate", "--run", str(output)]) == 0
    capsys.readouterr()
    (output / "proposals.json").write_text("[] ", encoding="utf-8")
    assert cli_main(["evaluate", "--run", str(output)]) == 2
    assert "checksum mismatch" in capsys.readouterr().err



def test_existing_run_directory_is_never_overwritten(
    private_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = read_snapshot(
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.selection,
    )
    fixed_id = UUID("00000000-0000-4000-8000-000000000001")
    destination = private_fixture.annotation_root / "benchmarks" / "runs" / str(fixed_id)
    destination.mkdir(parents=True)
    sentinel = destination / "keep.txt"
    sentinel.write_text("original")
    monkeypatch.setattr("app.annotation_benchmark.runner.uuid4", lambda: fixed_id)
    monkeypatch.setattr("app.annotation_benchmark.runner.load_detector", lambda *args: _Detector())
    monkeypatch.setattr(
        "app.annotation_benchmark.runner.iter_source_frames",
        lambda source, segment, config, **kwargs: _frames(segment, config),
    )
    _use_inline_worker(monkeypatch)

    with pytest.raises(BenchmarkRunError, match="output_collision"):
        run_benchmark(
            manifest,
            RunConfig(output_quota_bytes=1024 * 1024, free_disk_reserve_bytes=0),
            "mediapipe",
            _asset(),
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.source_root,
        )

    assert sentinel.read_text() == "original"


def test_failure_never_publishes_completed_run(
    private_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = read_snapshot(
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.selection,
    )
    detector = _Detector(error=MemoryError("out_of_memory"))
    before = private_fixture.logical_dump()
    monkeypatch.setattr("app.annotation_benchmark.runner.load_detector", lambda *args: detector)
    monkeypatch.setattr(
        "app.annotation_benchmark.runner.iter_source_frames",
        lambda source, segment, config, **kwargs: _frames(segment, config),
    )
    _use_inline_worker(monkeypatch)

    with pytest.raises(BenchmarkRunError, match="out_of_memory"):
        run_benchmark(
            manifest,
            RunConfig(output_quota_bytes=1024 * 1024, free_disk_reserve_bytes=0),
            "mediapipe",
            _asset(),
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.source_root,
        )

    root = private_fixture.annotation_root / "benchmarks"
    assert not list((root / "runs").glob("*"))
    failures = list((root / "failed").glob("*"))
    assert len(failures) == 1
    status = json.loads((failures[0] / "status.json").read_text())
    assert status["state"] == "failed"
    assert status["reason"] == "out_of_memory"
    assert detector.closed
    assert private_fixture.logical_dump() == before


def test_stale_snapshot_is_rejected_before_detector_load(
    private_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = read_snapshot(
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.selection,
    )
    private_fixture.source_path.write_bytes(b"changed")
    called = False

    def load(*args):
        nonlocal called
        called = True
        return _Detector()

    monkeypatch.setattr("app.annotation_benchmark.runner.load_detector", load)
    with pytest.raises(BenchmarkRunError, match="stale_snapshot"):
        run_benchmark(
            manifest,
            RunConfig(output_quota_bytes=1024 * 1024, free_disk_reserve_bytes=0),
            "mediapipe",
            _asset(),
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.source_root,
        )
    assert not called


def test_public_runner_uses_worker_boundary(
    private_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = read_snapshot(
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.selection,
    )
    detector = _Detector()
    calls = []
    sync_calls = []
    monkeypatch.setattr("app.annotation_benchmark.runner.load_detector", lambda *args: detector)
    monkeypatch.setattr(
        "app.annotation_benchmark.runner.iter_source_frames",
        lambda source, segment, config, **kwargs: _frames(segment, config),
    )
    monkeypatch.setattr(runner_module, "_synchronize_cuda", lambda: sync_calls.append(1))

    real_worker = runner_module._perform_inference

    def execute(stage, *args):
        calls.append(stage)
        return real_worker(stage, *args)

    monkeypatch.setattr(runner_module, "_execute_worker", execute)
    run_benchmark(
        manifest,
        RunConfig(
            output_quota_bytes=1024 * 1024,
            free_disk_reserve_bytes=0,
            device="cuda:0",
        ),
        "mediapipe",
        _asset(),
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.source_root,
    )
    assert len(calls) == 1
    assert len(sync_calls) == 62


def test_running_v1_job_blocks_benchmark_before_model_load(
    private_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = read_snapshot(
        private_fixture.source_root,
        private_fixture.annotation_root,
        private_fixture.selection,
    )
    connection = sqlite3.connect(private_fixture.jobs_path)
    connection.execute(
        "UPDATE v1_tracking_jobs SET status='processing', stage='tracking' WHERE id=?",
        (str(private_fixture.source_job_id),),
    )
    connection.commit()
    connection.close()
    called = False

    def execute(*args):
        nonlocal called
        called = True

    monkeypatch.setattr(runner_module, "_execute_worker", execute)
    with pytest.raises(BenchmarkRunError, match="v1_busy"):
        run_benchmark(
            manifest,
            RunConfig(output_quota_bytes=1024 * 1024, free_disk_reserve_bytes=0),
            "mediapipe",
            _asset(),
            private_fixture.source_root,
            private_fixture.annotation_root,
            private_fixture.source_root,
        )
    assert not called
