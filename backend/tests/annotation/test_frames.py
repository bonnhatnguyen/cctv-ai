from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import pytest

from app.annotation.contracts import RegisterClip
from app.annotation.frames import FrameIntegrityError, FrameOutOfRange, FrameService, SourceChanged
from app.annotation.media import prepare_clip
from app.v1.media import probe_video


def _ready_service(tmp_path: Path, make_numbered_source, repo, jobs):
    job_id = str(uuid4())
    source = make_numbered_source(
        tmp_path / "source-data" / "jobs" / job_id / "source.mp4", frames=8
    )
    metadata = probe_video(source)
    job = jobs.create_imported(job_id, "frames.mp4", source, metadata)
    clip = repo.register_clip(RegisterClip(operation_id=uuid4(), source_job_id=job.id))
    staging = tmp_path / "staging-frames"
    prepared = prepare_clip(
        source, staging, metadata.fps_num, metadata.fps_den, metadata.sample_aspect_ratio
    )
    service = FrameService(repo.database.root, repo, cache_limit_bytes=100_000)
    return source, service, service.publish(clip.id, prepared, staging)


def test_frame_bounds_are_integer_ordinals(tmp_path, make_numbered_source, repo, jobs):
    _, service, clip = _ready_service(tmp_path, make_numbered_source, repo, jobs)
    with pytest.raises(FrameOutOfRange):
        service.read_frame(clip.id, -1)
    with pytest.raises(FrameOutOfRange):
        service.read_frame(clip.id, clip.media.frame_count)


def test_corrupt_chunk_never_returns_a_png(tmp_path, make_numbered_source, repo, jobs):
    _, service, clip = _ready_service(tmp_path, make_numbered_source, repo, jobs)
    chunk = service.prepared_root(clip.id) / "chunks" / "000000.mkv"
    chunk.write_bytes(b"corrupt")
    with pytest.raises(FrameIntegrityError):
        service.read_frame(clip.id, 0)


def test_source_change_invalidates_cached_frame(tmp_path, make_numbered_source, repo, jobs):
    source, service, clip = _ready_service(tmp_path, make_numbered_source, repo, jobs)
    service.read_frame(clip.id, 0)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(SourceChanged):
        service.read_frame(clip.id, 0)
    assert repo.get_clip(clip.id).source_state == "hash_mismatch"


def test_adjacent_frames_decode_a_lossless_chunk_only_once(tmp_path, make_numbered_source, repo, jobs):
    _, service, clip = _ready_service(tmp_path, make_numbered_source, repo, jobs)
    service.cache_limit_bytes = 32 * 1024 * 1024
    with patch("app.annotation.frames.subprocess.run", wraps=__import__("subprocess").run) as run:
        service.read_frame(clip.id, 0)
        service.read_frame(clip.id, 1)
    assert run.call_count == 1
