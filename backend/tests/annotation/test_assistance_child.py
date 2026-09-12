from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.annotation.assistance_child import run_child
from app.annotation.assistance_protocol import AssistanceChildRequest, AssistanceChildResult


def test_child_writes_a_complete_atomic_result(tmp_path: Path, monkeypatch):
    import app.annotation.assistance_child as child

    source = (tmp_path / "source.mp4").resolve()
    source.write_bytes(b"video")
    model_root = (tmp_path / "model").resolve()
    model_root.mkdir()
    request = AssistanceChildRequest(
        run_id=uuid4(), clip_id=uuid4(), source_job_id=uuid4(),
        source_path=source, source_sha256="1" * 64, roi_revision_id=uuid4(),
        polygon=[[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
        frame_count=20, width=640, height=360, fps_num=25, fps_den=1,
        sar_num=1, sar_den=1, start_frame=0, end_frame=10,
        model="dino", model_root=model_root, device="cpu", stride=5,
    )
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")

    class Detector:
        def detect(self, frame):
            return []
        def close(self):
            pass

    frames = [type("Frame", (), {"source_index": index})() for index in (0, 5, 10)]
    monkeypatch.setattr(child, "_load_detector", lambda request: Detector())
    monkeypatch.setattr(child, "_iter_frames", lambda request: frames)

    assert run_child(request_path, result_path) == 0
    result = AssistanceChildResult.model_validate_json(result_path.read_bytes())
    assert result.scheduled_frames == [0, 5, 10]
    assert result.observed_frames == [0, 5, 10]
    assert not (tmp_path / "result.json.tmp").exists()
