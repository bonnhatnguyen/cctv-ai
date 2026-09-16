from __future__ import annotations

import hashlib
import math
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .contracts import FrozenSegment, RunConfig


class SourceMediaChanged(RuntimeError):
    pass


class SourceDecodeError(RuntimeError):
    pass


class SourceDecodeCancelled(SourceDecodeError):
    pass


@dataclass(frozen=True)
class CropTransform:
    left: int
    top: int
    right: int
    bottom: int
    input_width: int
    input_height: int
    source_width: int
    source_height: int

    def source_xy(self, x: float, y: float) -> tuple[float, float]:
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError("model coordinates must be normalized")
        source_x = self.left + x * (self.right - self.left)
        source_y = self.top + y * (self.bottom - self.top)
        return source_x / self.source_width, source_y / self.source_height


@dataclass(frozen=True)
class SampledFrame:
    source_index: int
    timestamp_ms: int
    rgb: np.ndarray
    transform: CropTransform


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_timestamp_ms(
    index: int,
    fps_num: int,
    fps_den: int,
    previous_ms: int | None,
) -> int:
    if index < 0 or fps_num <= 0 or fps_den <= 0:
        raise ValueError("frame timing values must be positive")
    derived = index * 1000 * fps_den // fps_num
    return derived if previous_ms is None else max(derived, previous_ms + 1)


def _crop_transform(segment: FrozenSegment, config: RunConfig) -> CropTransform:
    xs = [point[0] * segment.width for point in segment.polygon]
    ys = [point[1] * segment.height for point in segment.polygon]
    roi_width = max(xs) - min(xs)
    roi_height = max(ys) - min(ys)
    left = max(0, math.floor(min(xs) - roi_width * config.margin_ratio))
    top = max(0, math.floor(min(ys) - roi_height * config.margin_ratio))
    right = min(segment.width, math.ceil(max(xs) + roi_width * config.margin_ratio))
    bottom = min(segment.height, math.ceil(max(ys) + roi_height * config.margin_ratio))
    if right <= left or bottom <= top:
        raise SourceDecodeError("ROI crop has no raster area")
    display_width = (right - left) * segment.sar_num / segment.sar_den
    display_height = bottom - top
    scale = min(1.0, config.max_input_side / max(display_width, display_height))
    input_width = max(1, round(display_width * scale))
    input_height = max(1, round(display_height * scale))
    return CropTransform(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        input_width=input_width,
        input_height=input_height,
        source_width=segment.width,
        source_height=segment.height,
    )


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def _read_exact(stream, size: int) -> bytes:
    blocks = bytearray()
    while len(blocks) < size:
        block = stream.read(size - len(blocks))
        if not block:
            break
        blocks.extend(block)
    return bytes(blocks)


def iter_source_frames(
    source: Path,
    segment: FrozenSegment,
    config: RunConfig,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> Iterator[SampledFrame]:
    source = source.resolve()
    cancel_requested = cancel_requested or (lambda: False)
    try:
        if _sha256(source) != segment.source_sha256:
            raise SourceMediaChanged("source video hash differs from the frozen snapshot")
    except OSError as exc:
        raise SourceMediaChanged("source video is unavailable") from exc

    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-pix_fmt",
        "rgb24",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=_startupinfo(),
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise SourceDecodeError("decoder pipes are unavailable")
    frame_bytes = segment.width * segment.height * 3
    transform = _crop_transform(segment, config)
    scheduled = set(
        range(
            segment.selection.span.start_frame,
            segment.selection.span.end_frame + 1,
            config.stride,
        )
    )
    deadline = time.monotonic() + config.segment_deadline_seconds
    last_decoder_activity = time.monotonic()
    previous_timestamp: int | None = None
    decoded = 0
    error: BaseException | None = None
    reader_stopped = threading.Event()
    frames: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=2)
    stderr_tail = bytearray()

    def enqueue(item: bytes | BaseException | None) -> None:
        while not reader_stopped.is_set():
            try:
                frames.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read_frames() -> None:
        try:
            while not reader_stopped.is_set():
                raw = _read_exact(process.stdout, frame_bytes)
                if not raw:
                    enqueue(None)
                    return
                enqueue(raw)
        except BaseException as exc:
            enqueue(exc)

    def read_stderr() -> None:
        while True:
            block = process.stderr.read(4096)
            if not block:
                return
            stderr_tail.extend(block)
            if len(stderr_tail) > 64 * 1024:
                del stderr_tail[: len(stderr_tail) - 64 * 1024]

    frame_reader = threading.Thread(target=read_frames, daemon=True)
    error_reader = threading.Thread(target=read_stderr, daemon=True)
    frame_reader.start()
    error_reader.start()
    try:
        while True:
            if cancel_requested():
                raise SourceDecodeCancelled("source decode was cancelled")
            now = time.monotonic()
            if now > deadline:
                raise SourceDecodeError("source decoder exceeded its segment deadline")
            if now - last_decoder_activity >= config.decoder_stall_seconds:
                raise SourceDecodeError("source decoder stalled")
            try:
                item = frames.get(timeout=0.1)
            except queue.Empty:
                continue
            last_decoder_activity = time.monotonic()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise SourceDecodeError("source decoder reader failed") from item
            raw = item
            if len(raw) != frame_bytes:
                raise SourceDecodeError("source decoder returned a partial frame")
            if decoded in scheduled:
                raster = np.frombuffer(raw, dtype=np.uint8).reshape(
                    segment.height, segment.width, 3
                )
                crop = raster[
                    transform.top : transform.bottom,
                    transform.left : transform.right,
                ]
                image = Image.fromarray(crop, mode="RGB")
                if image.size != (transform.input_width, transform.input_height):
                    image = image.resize(
                        (transform.input_width, transform.input_height),
                        Image.Resampling.BILINEAR,
                    )
                timestamp = model_timestamp_ms(
                    decoded,
                    segment.fps_num,
                    segment.fps_den,
                    previous_timestamp,
                )
                previous_timestamp = timestamp
                yield SampledFrame(
                    source_index=decoded,
                    timestamp_ms=timestamp,
                    rgb=np.asarray(image).copy(),
                    transform=transform,
                )
            decoded += 1
    except BaseException as exc:
        error = exc
        raise
    finally:
        reader_stopped.set()
        if process.poll() is None:
            if error is not None:
                process.kill()
                try:
                    process.wait(timeout=config.decoder_stall_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            else:
                try:
                    process.wait(timeout=config.decoder_stall_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    error = SourceDecodeError("source decoder did not exit")
        frame_reader.join(timeout=0.2)
        error_reader.join(timeout=0.2)
        stderr = stderr_tail.decode("utf-8", errors="replace")
        process.stdout.close()
        process.stderr.close()
        if error is not None and isinstance(error, SourceDecodeError) and str(error) == "source decoder did not exit":
            raise error
        if error is None and process.returncode:
            raise SourceDecodeError(f"source decoder failed: {stderr[-1000:]}")

    if decoded != segment.frame_count:
        raise SourceDecodeError(
            f"source decoder returned {decoded} frames; expected {segment.frame_count}"
        )
    try:
        if _sha256(source) != segment.source_sha256:
            raise SourceMediaChanged("source video hash changed during decode")
    except OSError as exc:
        raise SourceMediaChanged("source video became unavailable during decode") from exc
