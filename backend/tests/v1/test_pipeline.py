from __future__ import annotations

import cv2
import numpy as np


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
