from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from app.v1.media import probe_video


class PreparationError(RuntimeError):
    pass


class PreparationTimeout(PreparationError):
    pass


class PreparationCancelled(PreparationError):
    pass


class StorageFull(PreparationError):
    pass


@dataclass(frozen=True)
class PreparedMedia:
    source_sha256: str
    frame_count: int
    width: int
    height: int
    fps_num: int
    fps_den: int
    sample_aspect_ratio: str
    artifact_manifest: dict
    prepared_bytes: int


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def _sha256_file(path: Path, cancel_requested: Callable[[], bool] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            if cancel_requested is not None and cancel_requested():
                raise PreparationCancelled("media preparation was cancelled")
            digest.update(block)
    return digest.hexdigest()


def _drain_bounded(stream: BinaryIO, target: bytearray, limit: int = 64 * 1024) -> None:
    while True:
        block = stream.read(4096)
        if not block:
            return
        target.extend(block)
        if len(target) > limit:
            del target[: len(target) - limit]


def _run(
    command: list[str],
    *,
    input_bytes: bytes,
    timeout: float,
    cancel_requested: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    cancel_requested = cancel_requested or (lambda: False)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=_hidden_startupinfo(),
    )
    deadline = time.monotonic() + max(0.1, timeout)
    pending_input: bytes | None = input_bytes
    while True:
        if cancel_requested():
            process.kill()
            process.communicate()
            raise PreparationCancelled("media subprocess was cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            raise PreparationTimeout("media subprocess exceeded its deadline")
        try:
            stdout, stderr = process.communicate(
                input=pending_input, timeout=min(0.1, remaining)
            )
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            pending_input = None


def _encode_chunk(
    chunk_path: Path,
    frames: list[bytes],
    width: int,
    height: int,
    fps_num: int,
    fps_den: int,
    expected_hashes: list[str],
    timeout: float,
    cancel_requested: Callable[[], bool],
) -> str:
    frame_rate = f"{fps_num}/{fps_den}"
    encoded = _run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", frame_rate, "-i", "pipe:0", "-an",
            "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", "-y", str(chunk_path),
        ],
        input_bytes=b"".join(frames),
        timeout=timeout,
        cancel_requested=cancel_requested,
    )
    if encoded.returncode:
        raise PreparationError("lossless chunk encoder failed: " + encoded.stderr[-4096:].decode("utf-8", "replace"))
    decoded = _run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(chunk_path),
            "-map", "0:v:0", "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ],
        input_bytes=b"",
        timeout=timeout,
        cancel_requested=cancel_requested,
    )
    if decoded.returncode:
        raise PreparationError("lossless chunk verification decode failed")
    frame_bytes = width * height * 3
    if len(decoded.stdout) != len(frames) * frame_bytes:
        raise PreparationError("lossless chunk verification frame count mismatch")
    actual_hashes = [
        hashlib.sha256(decoded.stdout[offset : offset + frame_bytes]).hexdigest()
        for offset in range(0, len(decoded.stdout), frame_bytes)
    ]
    if actual_hashes != expected_hashes:
        raise PreparationError("lossless chunk verification hash mismatch")
    return _sha256_file(chunk_path)


def _frame_reader(stream: BinaryIO, frame_bytes: int, output: queue.Queue[bytes | BaseException | None]) -> None:
    try:
        while True:
            frame = bytearray()
            while len(frame) < frame_bytes:
                block = stream.read(frame_bytes - len(frame))
                if not block:
                    if frame:
                        raise PreparationError("source decoder returned a partial frame")
                    output.put(None)
                    return
                frame.extend(block)
            output.put(bytes(frame))
    except BaseException as exc:
        output.put(exc)


def prepare_clip(
    source: Path,
    staging: Path,
    fps_num: int,
    fps_den: int,
    sample_aspect_ratio: str,
    *,
    stall_timeout_seconds: float = 120,
    deadline_seconds: float = 3600,
    max_output_bytes: int | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> PreparedMedia:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    metadata = probe_video(source)
    if (metadata.fps_num, metadata.fps_den) != (fps_num, fps_den):
        raise PreparationError("source frame rate changed before preparation")
    if metadata.sample_aspect_ratio != sample_aspect_ratio:
        raise PreparationError("source sample aspect ratio changed before preparation")
    if staging.exists():
        raise FileExistsError(staging)
    chunks_dir = staging / "chunks"
    chunks_dir.mkdir(parents=True)
    if max_output_bytes is not None and max_output_bytes <= 0:
        raise StorageFull("annotation prepared-media quota is full")
    cancel_requested = cancel_requested or (lambda: False)
    source_sha256 = _sha256_file(source, cancel_requested)
    frame_bytes = metadata.width * metadata.height * 3
    frame_rate = f"{fps_num}/{fps_den}"
    chunk_frame_limit = max(1, math.ceil(fps_num / fps_den))
    preview_path = staging / "preview.mp4"
    source_process = subprocess.Popen(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(source),
            "-map", "0:v:0", "-an", "-fps_mode", "passthrough", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=_hidden_startupinfo(),
    )
    preview_process = subprocess.Popen(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{metadata.width}x{metadata.height}", "-r", frame_rate, "-i", "pipe:0",
            "-an", "-vf", f"setsar={sample_aspect_ratio.replace(':', '/')}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-g", str(chunk_frame_limit), "-movflags", "+faststart", "-y", str(preview_path),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=_hidden_startupinfo(),
    )
    assert source_process.stdout is not None and source_process.stderr is not None
    assert preview_process.stdin is not None and preview_process.stderr is not None
    source_stderr = bytearray()
    preview_stderr = bytearray()
    threading.Thread(target=_drain_bounded, args=(source_process.stderr, source_stderr), daemon=True).start()
    threading.Thread(target=_drain_bounded, args=(preview_process.stderr, preview_stderr), daemon=True).start()
    frames_queue: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=2)
    reader = threading.Thread(
        target=_frame_reader, args=(source_process.stdout, frame_bytes, frames_queue), daemon=True
    )
    reader.start()
    started = time.monotonic()
    last_progress = started
    frame_hashes: list[str] = []
    chunks: list[dict] = []
    chunk_frames: list[bytes] = []
    chunk_hashes: list[str] = []

    def remaining() -> float:
        if cancel_requested():
            raise PreparationCancelled("media preparation was cancelled")
        value = deadline_seconds - (time.monotonic() - started)
        if value <= 0:
            raise PreparationTimeout("media preparation exceeded its total deadline")
        return value

    def enforce_storage_budget() -> None:
        if max_output_bytes is None:
            return
        used = sum(path.stat().st_size for path in staging.rglob("*") if path.is_file())
        if used > max_output_bytes:
            raise StorageFull("annotation prepared-media quota is full")


    def flush_chunk() -> None:
        if not chunk_frames:
            return
        start = len(frame_hashes) - len(chunk_frames)
        filename = f"{len(chunks):06d}.mkv"
        path = chunks_dir / filename
        file_hash = _encode_chunk(
            path,
            chunk_frames,
            metadata.width,
            metadata.height,
            fps_num,
            fps_den,
            chunk_hashes,
            remaining(),
            cancel_requested,
        )
        chunks.append(
            {
                "filename": filename,
                "start_frame": start,
                "frame_count": len(chunk_frames),
                "sha256": file_hash,
            }
        )
        enforce_storage_budget()
        chunk_frames.clear()
        chunk_hashes.clear()

    try:
        while True:
            stall_remaining = stall_timeout_seconds - (time.monotonic() - last_progress)
            if stall_remaining <= 0:
                raise PreparationTimeout("source decoder made no frame progress")
            wait_for = min(stall_remaining, remaining(), 0.25)
            try:
                item = frames_queue.get(timeout=wait_for)
            except queue.Empty:
                continue
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            last_progress = time.monotonic()
            digest = hashlib.sha256(item).hexdigest()
            frame_hashes.append(digest)
            chunk_hashes.append(digest)
            chunk_frames.append(item)
            if cancel_requested():
                raise PreparationCancelled("media preparation was cancelled")
            preview_process.stdin.write(item)
            if len(chunk_frames) == chunk_frame_limit:
                flush_chunk()
        flush_chunk()
        preview_process.stdin.close()
        source_returncode = source_process.wait(timeout=max(0.1, remaining()))
        preview_returncode = preview_process.wait(timeout=max(0.1, remaining()))
        if source_returncode:
            raise PreparationError("source decoder failed: " + source_stderr[-4096:].decode("utf-8", "replace"))
        if preview_returncode:
            raise PreparationError("clean preview encoder failed: " + preview_stderr[-4096:].decode("utf-8", "replace"))
        if not frame_hashes:
            raise PreparationError("source contains no decoded frames")
        preview_metadata = probe_video(preview_path)
        if preview_metadata.frame_count_estimate != len(frame_hashes):
            raise PreparationError("clean preview frame count mismatch")
        if (preview_metadata.fps_num, preview_metadata.fps_den) != (fps_num, fps_den):
            raise PreparationError("clean preview frame rate mismatch")
        if preview_metadata.sample_aspect_ratio != sample_aspect_ratio:
            raise PreparationError("clean preview sample aspect ratio mismatch")
        manifest = {
            "schema_version": 1,
            "source_sha256": source_sha256,
            "frame_count": len(frame_hashes),
            "width": metadata.width,
            "height": metadata.height,
            "fps_num": fps_num,
            "fps_den": fps_den,
            "sample_aspect_ratio": sample_aspect_ratio,
            "frame_sha256": frame_hashes,
            "chunks": chunks,
            "preview": {"filename": "preview.mp4", "sha256": _sha256_file(preview_path)},
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        enforce_storage_budget()
        prepared_bytes = sum(path.stat().st_size for path in staging.rglob("*") if path.is_file())
        return PreparedMedia(
            source_sha256=source_sha256,
            frame_count=len(frame_hashes),
            width=metadata.width,
            height=metadata.height,
            fps_num=fps_num,
            fps_den=fps_den,
            sample_aspect_ratio=sample_aspect_ratio,
            artifact_manifest=manifest,
            prepared_bytes=prepared_bytes,
        )
    except BaseException:
        for process in (source_process, preview_process):
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        raise
    finally:
        if preview_process.stdin and not preview_process.stdin.closed:
            preview_process.stdin.close()
        if source_process.stdout:
            source_process.stdout.close()
        reader.join(timeout=1)
