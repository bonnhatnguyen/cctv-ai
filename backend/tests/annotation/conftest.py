from __future__ import annotations

from pathlib import Path
import json
import subprocess
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from app.v1.contracts import VideoMetadata
from app.v1.database import create_database
from app.v1.jobs import JobRepository


@pytest.fixture
def source_data_root(tmp_path: Path) -> Path:
    root = (tmp_path / "source-data").resolve()
    root.mkdir()
    return root


@pytest.fixture
def jobs(source_data_root: Path) -> JobRepository:
    database = create_database(f"sqlite:///{(source_data_root / 'jobs.db').as_posix()}")
    database.create_schema()
    return JobRepository(database.sessions)


@pytest.fixture
def imported_job(jobs: JobRepository, source_data_root: Path):
    job_id = str(uuid4())
    source = source_data_root / "jobs" / job_id / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"private-test-video")
    return jobs.create_imported(
        job_id,
        "checkout.mp4",
        source,
        VideoMetadata(
            size_bytes=source.stat().st_size,
            width=640,
            height=360,
            duration_ms=120,
            fps_num=25,
            fps_den=1,
            frame_count_estimate=3,
            codec="h264",
            preview_supported=True,
        ),
    )


@pytest.fixture
def repo(tmp_path: Path, source_data_root: Path, jobs: JobRepository):
    from app.annotation.database import AnnotationDatabase
    from app.annotation.repository import AnnotationRepository

    database = AnnotationDatabase(tmp_path / "annotations", source_data_root)
    database.initialize()
    repository = AnnotationRepository(database, jobs)
    try:
        yield repository
    finally:
        database.close()


@pytest.fixture
def registered_clip(repo, imported_job):
    from app.annotation.contracts import MediaView, RegisterClip

    clip = repo.register_clip(
        RegisterClip(operation_id=uuid4(), source_job_id=imported_job.id)
    )
    return repo.complete_preparation(
        clip.id,
        source_sha256="1" * 64,
        media=MediaView(
            frame_count=3,
            fps_num=25,
            fps_den=1,
            width=640,
            height=360,
            sample_aspect_ratio="1:1",
        ),
        artifact_manifest={"schema_version": 1, "chunks": []},
        prepared_bytes=123,
    )


@pytest.fixture
def setup(repo):
    from app.annotation.contracts import CameraSetupCreate

    return repo.create_setup(CameraSetupCreate(operation_id=uuid4(), name="Quầy 1"))


@pytest.fixture
def make_numbered_source():
    def make(
        path: Path,
        *,
        frames: int,
        fps_num: int = 25,
        fps_den: int = 1,
        sar: str = "1:1",
        width: int = 160,
        height: int = 90,
        codec: str = "libx264",
    ) -> Path:
        payload = bytearray()
        for index in range(frames):
            image = Image.new("RGB", (width, height), (index * 17 % 256, index * 29 % 256, index * 43 % 256))
            draw = ImageDraw.Draw(image)
            draw.rectangle((4, 4, 75, 40), fill=(0, 0, 0))
            draw.text((8, 8), f"FRAME {index:04d}", fill=(255, 255, 255))
            for bit in range(8):
                color = (255, 255, 255) if index & (1 << bit) else (0, 0, 0)
                draw.rectangle((8 + bit * 16, 55, 19 + bit * 16, 78), fill=color)
            payload.extend(image.tobytes())
        path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", f"{fps_num}/{fps_den}", "-i", "pipe:0",
            "-an", "-vf", f"setsar={sar.replace(':', '/')}", "-c:v", codec,
        ]
        if codec == "libx264":
            command += ["-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "12"]
        command += ["-y", str(path)]
        completed = subprocess.run(command, input=bytes(payload), capture_output=True, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
        return path

    return make


@pytest.fixture
def decode_source_rgb():
    def decode(path: Path) -> list[bytes]:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True,
            check=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        frame_bytes = int(stream["width"]) * int(stream["height"]) * 3
        completed = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            capture_output=True,
            check=True,
        )
        assert len(completed.stdout) % frame_bytes == 0
        return [
            completed.stdout[offset : offset + frame_bytes]
            for offset in range(0, len(completed.stdout), frame_bytes)
        ]

    return decode
