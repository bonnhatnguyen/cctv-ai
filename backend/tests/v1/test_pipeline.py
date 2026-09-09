from __future__ import annotations

import cv2
import hashlib
import numpy as np
import os
import pytest
import subprocess
import sys
import time
from dataclasses import replace


def _tiny_source(path):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (640, 360))
    for index in range(3):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[:, :, 1] = index * 40
        writer.write(frame)
    writer.release()


class _NoopTracker:
    resolved_device = actual_device = "cpu"
    device_name = "CPU"

    def __init__(self, *_args, **_kwargs):
        pass

    def track_frame(self, _frame):
        from app.v1.contracts import FrameTracking

        return FrameTracking([], None, 1.0)


def test_process_video_encodes_all_frames_and_writes_frame_evidence(tmp_path, monkeypatch):
    from app.v1.contracts import FrameTracking, RunOptions
    from app.v1 import pipeline
    from app.v1.contracts import TrackedPerson

    source = tmp_path / "source.mp4"
    output = tmp_path / "result.mp4"
    evidence = tmp_path / "evidence.jsonl"
    _tiny_source(source)

    class FakeTracker:
        resolved_device = "cpu"
        actual_device = "cpu"
        device_name = "CPU"

        def __init__(self, *_args, **_kwargs):
            self.count = 0

        def track_frame(self, _frame):
            self.count += 1
            return FrameTracking([TrackedPerson(4, 0.9, (10, 20, 100, 200))], 5.0, 1.0)

    monkeypatch.setattr(pipeline, "PersonTracker", FakeTracker)
    summary = pipeline.process_video(source, output, RunOptions("unused", "cpu"), evidence_path=evidence)
    assert summary.processed_frames == 3
    assert summary.local_track_count == 1
    assert summary.inference_samples == 3
    capture = cv2.VideoCapture(str(output))
    frames = 0
    while capture.read()[0]:
        frames += 1
    capture.release()
    assert frames == 3
    assert len(evidence.read_text(encoding="utf-8").splitlines()) == 3


def test_processing_seconds_include_source_decode_before_tracking(tmp_path, monkeypatch):
    from app.v1 import pipeline
    from app.v1.contracts import RunOptions

    source = tmp_path / "source.mp4"
    output = tmp_path / "result.mp4"
    _tiny_source(source)
    original_decode = pipeline.fully_decode_video

    def delayed_decode(path):
        time.sleep(0.25)
        return original_decode(path)

    monkeypatch.setattr(pipeline, "fully_decode_video", delayed_decode)
    monkeypatch.setattr(pipeline, "PersonTracker", _NoopTracker)
    summary = pipeline.process_video(source, output, RunOptions("unused", "cpu"))
    assert summary.processing_seconds >= 0.20


def test_existing_evidence_is_rejected_before_processing_and_source_is_unchanged(tmp_path):
    from app.v1.contracts import RunOptions
    from app.v1.pipeline import process_video

    source = tmp_path / "source.mp4"
    source.write_bytes(b"original-source")
    evidence = tmp_path / "existing.jsonl"
    evidence.write_text("keep me", encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        process_video(source, tmp_path / "result.mp4", RunOptions("missing.pt"), evidence_path=evidence)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert evidence.read_text(encoding="utf-8") == "keep me"


def test_hardlink_evidence_alias_is_rejected_before_processing(tmp_path):
    from app.v1.contracts import RunOptions
    from app.v1.pipeline import process_video

    source = tmp_path / "source.mp4"
    source.write_bytes(b"original-source")
    evidence = tmp_path / "evidence.jsonl"
    os.link(source, evidence)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="different|alias"):
        process_video(source, tmp_path / "result.mp4", RunOptions("missing.pt"), evidence_path=evidence)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_output_hardlink_alias_is_rejected_before_processing(tmp_path):
    from app.v1.contracts import RunOptions
    from app.v1.pipeline import process_video

    source = tmp_path / "source.mp4"
    source.write_bytes(b"original-source")
    output = tmp_path / "result.mp4"
    os.link(source, output)
    with pytest.raises(ValueError, match="different|alias"):
        process_video(source, output, RunOptions("missing.pt"))


def test_decoder_stop_is_rejected_when_advertised_frame_count_is_unknown(tmp_path, monkeypatch):
    from app.v1 import media, pipeline
    from app.v1.contracts import FrameTracking, RunOptions

    source = tmp_path / "source.mp4"
    output = tmp_path / "result.mp4"
    _tiny_source(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata = media.probe_video(source)
    decoded = media.fully_decode_video(source)
    monkeypatch.setattr(pipeline, "probe_video", lambda _path: replace(metadata, frame_count_estimate=None))
    monkeypatch.setattr(pipeline, "fully_decode_video", lambda _path: decoded, raising=False)

    real_capture = cv2.VideoCapture

    class PrematureCapture:
        def __init__(self, path):
            self._capture = real_capture(path)
            self._reads = 0

        def isOpened(self):
            return self._capture.isOpened()

        def read(self):
            if self._reads == 2:
                return False, None
            self._reads += 1
            return self._capture.read()

        def release(self):
            self._capture.release()

    class FakeTracker:
        resolved_device = actual_device = "cpu"
        device_name = "CPU"

        def __init__(self, *_args, **_kwargs):
            pass

        def track_frame(self, _frame):
            return FrameTracking([], None, 1.0)

    monkeypatch.setattr(pipeline.cv2, "VideoCapture", PrematureCapture)
    monkeypatch.setattr(pipeline, "PersonTracker", FakeTracker)

    with pytest.raises(ValueError, match="decode|frame|trunc"):
        pipeline.process_video(source, output, RunOptions("unused", "cpu"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not output.exists()


def test_corrupt_mp4_failure_preserves_source_hash(tmp_path):
    from app.v1.contracts import RunOptions
    from app.v1.pipeline import process_video

    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"not an mp4")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        process_video(source, tmp_path / "result.mp4", RunOptions("missing.pt"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / "result.mp4").exists()


def test_missing_model_failure_preserves_source_hash(tmp_path):
    from app.v1.contracts import RunOptions
    from app.v1.pipeline import process_video

    source = tmp_path / "source.mp4"
    _tiny_source(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(FileNotFoundError, match="model"):
        process_video(source, tmp_path / "result.mp4", RunOptions(str(tmp_path / "missing.pt")))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / "result.mp4").exists()


def test_unreadable_model_failure_preserves_source_hash(tmp_path, monkeypatch):
    from app.v1 import pipeline
    from app.v1.contracts import RunOptions

    source = tmp_path / "source.mp4"
    _tiny_source(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    class UnreadableTracker:
        def __init__(self, *_args, **_kwargs):
            raise PermissionError("model is unreadable")

    monkeypatch.setattr(pipeline, "PersonTracker", UnreadableTracker)
    with pytest.raises(PermissionError, match="unreadable"):
        pipeline.process_video(source, tmp_path / "result.mp4", RunOptions("blocked.pt"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / "result.mp4").exists()


def test_encoder_failure_is_reported_and_preserves_source_hash(tmp_path, monkeypatch):
    from app.v1 import pipeline
    from app.v1.contracts import RunOptions

    source = tmp_path / "source.mp4"
    output = tmp_path / "result.mp4"
    _tiny_source(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    def failed_encoder(_output, _metadata):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdin.buffer.read(1); "
                "sys.stderr.write('forced encoder failure'); raise SystemExit(7)",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    monkeypatch.setattr(pipeline, "PersonTracker", _NoopTracker)
    monkeypatch.setattr(pipeline, "_start_encoder", failed_encoder)
    with pytest.raises(RuntimeError, match="encoder failed"):
        pipeline.process_video(source, output, RunOptions("unused", "cpu"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not output.exists()


def test_zero_frame_failure_preserves_source_hash(tmp_path, monkeypatch):
    from app.v1 import media, pipeline
    from app.v1.contracts import RunOptions

    source = tmp_path / "source.mp4"
    _tiny_source(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    decoded = media.fully_decode_video(source)
    monkeypatch.setattr(
        pipeline,
        "fully_decode_video",
        lambda _path: replace(decoded, decoded_frames=0, timestamps=()),
    )
    with pytest.raises(ValueError, match="no decodable frames"):
        pipeline.process_video(source, tmp_path / "result.mp4", RunOptions("unused", "cpu"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / "result.mp4").exists()


def test_separate_videos_get_fresh_tracker_and_summary_uses_measured_denominators(tmp_path, monkeypatch):
    from app.v1 import pipeline
    from app.v1.contracts import FrameTracking, RunOptions, TrackedPerson

    created = []

    class CountingTracker:
        resolved_device = actual_device = "cpu"
        device_name = "CPU"

        def __init__(self, *_args, **_kwargs):
            self.count = 0
            created.append(self)

        def track_frame(self, _frame):
            self.count += 1
            inference = (4.0, None, 8.0)[self.count - 1]
            return FrameTracking(
                [TrackedPerson(self.count, 0.9, (10, 20, 100, 200))],
                inference,
                float(self.count),
            )

    monkeypatch.setattr(pipeline, "PersonTracker", CountingTracker)
    summaries = []
    evidence_paths = []
    for index in range(2):
        source = tmp_path / f"source-{index}.mp4"
        output = tmp_path / f"result-{index}.mp4"
        evidence = tmp_path / f"evidence-{index}.jsonl"
        _tiny_source(source)
        summaries.append(
            pipeline.process_video(source, output, RunOptions("unused", "cpu"), evidence_path=evidence)
        )
        evidence_paths.append(evidence)

    assert len(created) == 2
    assert all('"track_id": 1' in path.read_text(encoding="utf-8").splitlines()[0] for path in evidence_paths)
    for summary in summaries:
        assert summary.inference_samples == 2
        assert summary.mean_inference_ms == 6.0
        assert summary.tracking_wall_ms_total == 6.0
        assert summary.effective_fps == pytest.approx(3 / summary.processing_seconds)
