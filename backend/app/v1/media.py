from __future__ import annotations

import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .contracts import TrackedPerson, VideoMetadata


def _ffprobe_json(path: Path) -> dict:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,width,height,duration,nb_frames,r_frame_rate,avg_frame_rate,sample_aspect_ratio,pix_fmt", "-of", "json", str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode or not completed.stdout:
        raise ValueError(f"unable to read video metadata: {path.name}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid metadata") from exc


def _ffprobe_timestamps(path: Path) -> list[float]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "frame=best_effort_timestamp_time", "-of", "json", str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode or not completed.stdout:
        raise ValueError(f"unable to read video frame timestamps: {path.name}")
    try:
        frames = json.loads(completed.stdout).get("frames") or []
        return [float(frame["best_effort_timestamp_time"]) for frame in frames]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("video frame timestamps are unavailable") from exc


def validate_cfr_timestamps(timestamps: list[float], fps_num: int, fps_den: int) -> None:
    """Reject VFR while allowing one known recorder startup near-duplicate.

    The initial CFR allowance is ±2 ms around the nominal interval.  Exactly
    one first delta <= 1.5 ms is permitted for the recorder's startup duplicate;
    every other delta must be within tolerance.  This accepts the approved
    baseline's 11 µs startup delta and 39/41 ms endpoint deltas.
    """
    if len(timestamps) < 2:
        return
    expected = fps_den / fps_num
    tolerance = max(0.002, expected * 0.025)
    startup_limit = min(0.0015, expected * 0.05)
    for index, (before, after) in enumerate(zip(timestamps, timestamps[1:])):
        delta = after - before
        if delta <= 0 or not math.isfinite(delta):
            raise ValueError("video has invalid frame timestamps")
        if abs(delta - expected) <= tolerance:
            continue
        if index == 0 and delta <= startup_limit:
            continue
        raise ValueError("variable frame timing is unsupported")


def _fraction(value: str | None, field: str) -> Fraction:
    if not value or value in {"0/0", "N/A"}:
        raise ValueError(f"video has no usable {field}")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"video has invalid {field}") from exc
    if result <= 0:
        raise ValueError(f"video has invalid {field}")
    return result


def probe_video(path: Path) -> VideoMetadata:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = _ffprobe_json(path)
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError("video contains no video stream")
    stream = streams[0]
    try:
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("video dimensions are unavailable") from exc
    if width <= 0 or height <= 0:
        raise ValueError("video dimensions are invalid")
    if width % 2 or height % 2:
        raise ValueError("odd video dimensions are unsupported")
    frame_rate = _fraction(stream.get("r_frame_rate") or stream.get("avg_frame_rate"), "frame rate")
    avg_rate_value = stream.get("avg_frame_rate")
    if avg_rate_value and avg_rate_value not in {"0/0", "N/A"}:
        average_rate = _fraction(avg_rate_value, "average frame rate")
        if abs(float(frame_rate - average_rate)) > max(0.05, float(frame_rate) * 0.01):
            raise ValueError("variable frame timing is unsupported")
    duration = float(stream.get("duration") or 0.0)
    if duration <= 0:
        raise ValueError("video duration is unavailable")
    frame_count: int | None
    try:
        frame_count = int(stream["nb_frames"]) if stream.get("nb_frames") not in {None, "N/A"} else None
    except (TypeError, ValueError):
        frame_count = None
    timestamps = _ffprobe_timestamps(path)
    if not timestamps:
        raise ValueError("video frame timestamps are unavailable")
    if frame_count is not None and timestamps and len(timestamps) != frame_count:
        raise ValueError("video frame timestamp count is inconsistent")
    validate_cfr_timestamps(timestamps, frame_rate.numerator, frame_rate.denominator)
    sar = stream.get("sample_aspect_ratio") or "1:1"
    return VideoMetadata(
        size_bytes=path.stat().st_size,
        width=width,
        height=height,
        duration_ms=round(duration * 1000),
        fps_num=frame_rate.numerator,
        fps_den=frame_rate.denominator,
        frame_count_estimate=frame_count,
        codec=str(stream.get("codec_name") or "unknown"),
        preview_supported=True,
        sample_aspect_ratio=str(sar),
    )


def unicode_font(size: int = 22) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def annotate_people(frame: np.ndarray, people: list[TrackedPerson]) -> np.ndarray:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3:
        raise ValueError("frame must be a color numpy array")
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    font = unicode_font(max(16, round(frame.shape[1] / 45)))
    for person in people:
        x1, y1, x2, y2 = (round(value) for value in person.xyxy)
        draw.rectangle((x1, y1, x2, y2), outline=(0, 230, 80), width=max(2, frame.shape[1] // 320))
        label = f"người #{person.track_id} · {person.confidence * 100:.0f}%"
        left, top, right, bottom = draw.textbbox((x1, max(0, y1 - 4)), label, font=font)
        label_top = max(0, y1 - (bottom - top) - 6)
        draw.rectangle((x1, label_top, right + 5, y1), fill=(0, 105, 40))
        draw.text((x1 + 2, label_top + 2), label, fill=(255, 255, 255), font=font)
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def output_stream_info(path: Path) -> dict:
    payload = _ffprobe_json(Path(path))
    streams = payload.get("streams") or []
    if not streams:
        raise ValueError("output contains no video stream")
    return streams[0]


def decoded_frame_count(path: Path) -> int:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"unable to decode video: {path.name}")
    count = 0
    try:
        while True:
            ok, _ = capture.read()
            if not ok:
                break
            count += 1
    finally:
        capture.release()
    return count


def validate_output(source: VideoMetadata, decoded_input_frames: int, output: Path) -> VideoMetadata:
    metadata = probe_video(output)
    info = output_stream_info(output)
    if metadata.width != source.width or metadata.height != source.height:
        raise ValueError("output resolution does not match source")
    if info.get("codec_name") != "h264" or info.get("pix_fmt") != "yuv420p":
        raise ValueError("output is not H.264/yuv420p")
    if decoded_frame_count(output) != decoded_input_frames:
        raise ValueError("output frame count does not match decoded input")
    source_frame_ms = 1000.0 * source.fps_den / source.fps_num
    if abs(metadata.duration_ms - source.duration_ms) > source_frame_ms + 50:
        raise ValueError("output duration does not match source")
    return metadata
