from __future__ import annotations

import hashlib
import sys
import threading
import time
import io
from pathlib import Path
from uuid import uuid4

from PIL import Image
import pytest

from app.annotation.contracts import RegisterClip
from app.annotation.frames import FrameService
from app.annotation.media import PreparationCancelled, _run, prepare_clip
from app.v1.media import probe_video


def test_owned_media_subprocess_is_cancelled_promptly():
    cancel = threading.Event()
    timer = threading.Timer(0.1, cancel.set)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(PreparationCancelled):
            _run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                input_bytes=b"",
                timeout=30,
                cancel_requested=cancel.is_set,
            )
    finally:
        timer.cancel()
    assert time.monotonic() - started < 2


def _register_video(repo, jobs, source: Path):
    job_id = source.parent.name
    job = jobs.create_imported(job_id, source.name, source, probe_video(source))
    return repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=job.id))


def test_exact_frames_survive_random_seek(
    tmp_path, make_numbered_source, decode_source_rgb, repo, jobs
):
    source = make_numbered_source(
        tmp_path / "source-data" / "jobs" / str(uuid4()) / "source.mp4",
        frames=30,
        sar="2:1",
    )
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata = probe_video(source)
    clip = _register_video(repo, jobs, source)
    staging = tmp_path / "staging"
    prepared = prepare_clip(
        source, staging, metadata.fps_num, metadata.fps_den, metadata.sample_aspect_ratio
    )
    reference = decode_source_rgb(source)
    service = FrameService(repo.database.root, repo)
    ready = service.publish(clip.id, prepared, staging)

    for index in [0, 29, 15, 14, 15, 1, 28]:
        actual = Image.open(io.BytesIO(service.read_frame(ready.id, index))).convert("RGB")
        assert actual.tobytes() == reference[index]
    assert ready.media.frame_count == 30
    assert ready.media.sample_aspect_ratio == "2:1"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_preview_is_clean_cfr_with_matching_sar(tmp_path, make_numbered_source, repo, jobs):
    source = make_numbered_source(
        tmp_path / "source-data" / "jobs" / str(uuid4()) / "source.mp4",
        frames=12,
        fps_num=30000,
        fps_den=1001,
        sar="2:1",
    )
    metadata = probe_video(source)
    clip = _register_video(repo, jobs, source)
    staging = tmp_path / "preview-staging"
    prepared = prepare_clip(
        source, staging, metadata.fps_num, metadata.fps_den, metadata.sample_aspect_ratio
    )
    service = FrameService(repo.database.root, repo)
    ready = service.publish(clip.id, prepared, staging)
    preview = service.preview_path(ready.id)
    preview_metadata = probe_video(preview)
    assert preview_metadata.frame_count_estimate == 12
    assert (preview_metadata.fps_num, preview_metadata.fps_den) == (30000, 1001)
    assert preview_metadata.sample_aspect_ratio == "2:1"
