"""Bounded streaming import into server-owned UUID directories."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterable, BinaryIO, Callable
from uuid import uuid4

import anyio
from fastapi import UploadFile
from python_multipart import MultipartParser
from python_multipart.multipart import MultipartParseError, parse_options_header

from .contracts import VideoMetadata
from .media import fully_decode_video, probe_video, quick_decode_video


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


def _open_private_upload(job_dir: Path, temporary: Path) -> BinaryIO:
    job_dir.mkdir(parents=True, exist_ok=False)
    return temporary.open("xb")


def _write_chunks(target: BinaryIO, chunks: tuple[bytes, ...]) -> None:
    for chunk in chunks:
        if target.write(chunk) != len(chunk):
            raise OSError("short upload write")


def _finish_upload(target: BinaryIO) -> None:
    target.flush()
    os.fsync(target.fileno())
    target.close()


def _close_upload(target: BinaryIO) -> None:
    try:
        target.close()
    except OSError:
        pass


def _remove_import_directory(job_dir: Path) -> None:
    shutil.rmtree(job_dir, ignore_errors=True)


class VideoStore:
    def __init__(
        self,
        root: Path,
        max_upload_bytes: int,
        *,
        probe: Callable[[Path], VideoMetadata] = probe_video,
        decode=quick_decode_video,
        chunk_size: int = 1024 * 1024,
    ):
        self.root = Path(root)
        self.max_upload_bytes = max_upload_bytes
        self._probe = probe
        self._decode = decode
        self._chunk_size = chunk_size

    def _validate_and_publish(
        self, job_id: str, original_name: str, temporary: Path, source: Path, written: int
    ) -> ImportedVideo:
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
            return self._validate_and_publish(job_id, original_name, temporary, source, written)
        except (UploadTooLargeError, UnsupportedVideoError):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        except (ValueError, subprocess.SubprocessError) as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise UnsupportedVideoError("unreadable video") from exc
        except OSError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise ImportStorageError("unable to store video") from exc

    async def import_multipart(
        self,
        stream: AsyncIterable[bytes],
        content_type: str,
        content_length: str | None = None,
        *,
        overhead_allowance: int = 64 * 1024,
    ) -> ImportedVideo:
        """Parse exactly one ``video`` file directly into private storage.

        File bytes are rejected by the incremental parser before an over-limit
        chunk is written. A separate request-body allowance also bounds headers,
        delimiters, and epilogue even when Content-Length is missing or false.
        """
        media_type, options = parse_options_header(content_type)
        boundary = options.get(b"boundary")
        if media_type != b"multipart/form-data" or not boundary:
            raise UnsupportedVideoError("multipart video required")
        max_body_bytes = self.max_upload_bytes + overhead_allowance
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise UnsupportedVideoError("invalid content length") from exc
            if declared_length < 0:
                raise UnsupportedVideoError("invalid content length")
            if declared_length > max_body_bytes:
                raise UploadTooLargeError()

        job_id = str(uuid4())
        job_dir = self.root / job_id
        temporary = job_dir / "source.upload.mp4"
        source = job_dir / "source.mp4"
        original_name: str | None = None
        target: BinaryIO | None = None
        current_header_name = bytearray()
        current_header_value = bytearray()
        disposition = b""
        part_count = 0
        completed = False
        file_declared = False
        file_bytes = 0
        body_bytes = 0
        pending_writes: list[bytes] = []
        published = False

        def on_part_begin() -> None:
            nonlocal disposition, part_count
            disposition = b""
            part_count += 1

        def on_header_field(data: bytes, start: int, end: int) -> None:
            current_header_name.extend(data[start:end])

        def on_header_value(data: bytes, start: int, end: int) -> None:
            current_header_value.extend(data[start:end])

        def on_header_end() -> None:
            nonlocal disposition
            if bytes(current_header_name).lower() == b"content-disposition":
                disposition = bytes(current_header_value)
            current_header_name.clear()
            current_header_value.clear()

        def on_headers_finished() -> None:
            nonlocal original_name, file_declared
            _kind, parameters = parse_options_header(disposition)
            field_name = parameters.get(b"name", b"").decode("utf-8", errors="replace")
            filename = parameters.get(b"filename")
            if part_count != 1 or field_name != "video" or filename is None:
                raise UnsupportedVideoError("exactly one video file is required")
            original_name = _safe_name(filename.decode("utf-8", errors="replace"))
            if Path(original_name).suffix.lower() != ".mp4":
                raise UnsupportedVideoError("MP4 required")
            file_declared = True

        def on_part_data(data: bytes, start: int, end: int) -> None:
            nonlocal file_bytes
            if not file_declared:
                raise UnsupportedVideoError("video file is unavailable")
            length = end - start
            if file_bytes + length > self.max_upload_bytes:
                raise UploadTooLargeError()
            pending_writes.append(bytes(data[start:end]))
            file_bytes += length

        def on_part_end() -> None:
            nonlocal completed
            completed = True

        callbacks = {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        }
        parser = MultipartParser(boundary, callbacks, max_header_count=8, max_header_size=4224)
        try:
            async for chunk in stream:
                body_bytes += len(chunk)
                if body_bytes > max_body_bytes:
                    raise UploadTooLargeError()
                parser.write(chunk)
                if file_declared and target is None:
                    target = await anyio.to_thread.run_sync(
                        _open_private_upload,
                        job_dir,
                        temporary,
                        abandon_on_cancel=False,
                    )
                if pending_writes:
                    writes = tuple(pending_writes)
                    pending_writes.clear()
                    assert target is not None
                    await anyio.to_thread.run_sync(
                        _write_chunks,
                        target,
                        writes,
                        abandon_on_cancel=False,
                    )
            parser.finalize()
            if target is None or original_name is None or not completed or part_count != 1:
                raise UnsupportedVideoError("incomplete multipart video")
            await anyio.to_thread.run_sync(_finish_upload, target, abandon_on_cancel=False)
            target = None
            imported = await anyio.to_thread.run_sync(
                self._validate_and_publish,
                job_id,
                original_name,
                temporary,
                source,
                file_bytes,
                abandon_on_cancel=False,
            )
            published = True
            return imported
        except (UploadTooLargeError, UnsupportedVideoError):
            raise
        except MultipartParseError as exc:
            raise UnsupportedVideoError("invalid multipart body") from exc
        except (ValueError, subprocess.SubprocessError) as exc:
            raise UnsupportedVideoError("unreadable video") from exc
        except OSError as exc:
            raise ImportStorageError("unable to store video") from exc
        finally:
            with anyio.CancelScope(shield=True):
                if target is not None:
                    await anyio.to_thread.run_sync(_close_upload, target, abandon_on_cancel=False)
                if not published:
                    await anyio.to_thread.run_sync(
                        _remove_import_directory, job_dir, abandon_on_cancel=False
                    )
