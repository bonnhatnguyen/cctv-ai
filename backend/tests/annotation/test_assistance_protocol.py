from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.annotation.assistance_protocol import AssistanceChildRequest, AssistanceChildResult


def _request(tmp_path: Path, **overrides):
    source = (tmp_path / "source.mp4").resolve()
    source.write_bytes(b"video")
    model_root = (tmp_path / "model").resolve()
    model_root.mkdir(exist_ok=True)
    payload = {
        "run_id": uuid4(), "clip_id": uuid4(), "source_job_id": uuid4(),
        "source_path": source, "source_sha256": "1" * 64,
        "roi_revision_id": uuid4(),
        "polygon": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
        "frame_count": 20, "width": 640, "height": 360,
        "fps_num": 25, "fps_den": 1, "sar_num": 1, "sar_den": 1,
        "start_frame": 0, "end_frame": 10, "model": "dino",
        "model_root": model_root, "device": "cpu", "stride": 5,
    }
    payload.update(overrides)
    return AssistanceChildRequest.model_validate(payload)


def test_child_request_rejects_urls_and_unknown_models(tmp_path: Path):
    with pytest.raises(ValidationError):
        _request(tmp_path, source_path="https://example.test/video.mp4")
    with pytest.raises(ValidationError):
        _request(tmp_path, model="module:evil")


def test_child_result_requires_every_scheduled_frame(tmp_path: Path):
    request = _request(tmp_path)
    with pytest.raises(ValidationError, match="scheduled"):
        AssistanceChildResult(
            run_id=request.run_id,
            scheduled_frames=[0, 5, 10],
            observed_frames=[0, 10],
            proposals=[],
        )
