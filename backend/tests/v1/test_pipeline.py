from __future__ import annotations

import cv2
import hashlib
import numpy as np
import os
import pytest


def _tiny_source(path):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (640, 360))
    for index in range(3):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[:, :, 1] = index * 40
        writer.write(frame)
    writer.release()


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
