"""Bounded streaming import into server-owned UUID directories."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fastapi import UploadFile

from .contracts import VideoMetadata
from .media import fully_decode_video, probe_video


class UnsupportedVideoError(ValueError):
    pass


class UploadTooLargeError(ValueError):
    pass


class ImportStorageError(OSError):
    pass


@dataclass(frozen=True)
class ImportedVideo:
    id: str
    original_name: str
    source: Path
    metadata: VideoMetadata


def _safe_name(filename: str | None) -> str:
    name = (filename or "video.mp4").replace("\\", "/").rsplit("/", 1)[-1]
    return (name or "video.mp4")[:255]


def _is_mp4_container(path: Path) -> bool:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    formats = {item.strip() for item in completed.stdout.split(",")}
    return completed.returncode == 0 and "mp4" in formats


class VideoStore:
    def __init__(
        self,
        root: Path,
        max_upload_bytes: int,
        *,
        probe: Callable[[Path], VideoMetadata] = probe_video,
        decode=fully_decode_video,
        chunk_size: int = 1024 * 1024,
    ):
        self.root = Path(root)
        self.max_upload_bytes = max_upload_bytes
        self._probe = probe
        self._decode = decode
        self._chunk_size = chunk_size

    def import_mp4(self, upload: UploadFile) -> ImportedVideo:
        original_name = _safe_name(upload.filename)
        if Path(original_name).suffix.lower() != ".mp4":
            raise UnsupportedVideoError("MP4 required")
        job_id = str(uuid4())
        job_dir = self.root / job_id
        temporary = job_dir / "source.upload.mp4"
        source = job_dir / "source.mp4"
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            written = 0
            with temporary.open("xb") as target:
                while True:
                    chunk = upload.file.read(self._chunk_size)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_upload_bytes:
                        raise UploadTooLargeError()
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if not _is_mp4_container(temporary):
                raise UnsupportedVideoError("unsupported container")
            metadata = self._probe(temporary)
            decoded = self._decode(temporary)
            if decoded.decoded_frames <= 0 or metadata.duration_ms <= 0:
                raise UnsupportedVideoError("empty video")
            if metadata.size_bytes != written:
                raise ImportStorageError("written byte count mismatch")
            os.replace(temporary, source)
            return ImportedVideo(job_id, original_name, source, metadata)
        except (UploadTooLargeError, UnsupportedVideoError):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        except (ValueError, subprocess.SubprocessError) as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise UnsupportedVideoError("unreadable video") from exc
        except OSError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise ImportStorageError("unable to store video") from exc
