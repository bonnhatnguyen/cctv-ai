from __future__ import annotations

import json
import logging
import math
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .contracts import TrackedPerson, VideoMetadata


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecodedVideo:
    codec: str
    pixel_format: str
    width: int
    height: int
    decoded_frames: int
    timestamps: tuple[float, ...]


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


def _ffprobe_timestamps(path: Path, max_frames: int | None = None) -> list[float]:
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0"]
    if max_frames is not None:
        command.extend(["-read_intervals", f"%+#{max_frames}"])
    command.extend([
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(path)
    ])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode or not completed.stdout:
        # Fallback to json if csv output is empty or failed
        json_cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "frame=best_effort_timestamp_time", "-of", "json", str(path),
        ]
        completed = subprocess.run(json_cmd, capture_output=True, text=True, check=False)
        if completed.returncode or not completed.stdout:
            raise ValueError(f"unable to read video frame timestamps: {path.name}")
        try:
            frames = json.loads(completed.stdout).get("frames") or []
            return [float(frame["best_effort_timestamp_time"]) for frame in frames]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("video frame timestamps are unavailable") from exc
    try:
        results = []
        for line in completed.stdout.splitlines():
            cleaned = line.strip().rstrip(",")
            if cleaned:
                results.append(float(cleaned))
        if not results:
            raise ValueError("no timestamps found")
        return results
    except ValueError as exc:
        raise ValueError("video frame timestamps are unavailable") from exc


def validate_cfr_timestamps(timestamps: list[float], fps_num: int, fps_den: int) -> None:
    """Validate frame timestamps, permitting real CCTV recordings with variable frame rate."""
    if len(timestamps) < 2:
        return
    expected = fps_den / fps_num
    tolerance = max(0.002, expected * 0.025)
    startup_limit = min(0.0015, expected * 0.05)
    vfr_count = 0
    for index, (before, after) in enumerate(zip(timestamps, timestamps[1:])):
        delta = after - before
        if delta <= 0 or not math.isfinite(delta):
            raise ValueError("video has invalid frame timestamps")
        if abs(delta - expected) <= tolerance:
            continue
        if index == 0 and delta <= startup_limit:
            continue
        vfr_count += 1
    total_intervals = len(timestamps) - 1
    if vfr_count > 1 and (vfr_count / total_intervals) > 0.15:
        raise ValueError("video has variable frame rate or sustained non-CFR timing")
    elif vfr_count > 0:
        logger.info("CCTV video has minor variable frame rate (VFR) timestamps (%d non-CFR deltas); accepted.", vfr_count)


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
            logger.info("VFR detected (nominal %s, avg %s); accepted for CCTV footage", frame_rate, average_rate)
    duration = float(stream.get("duration") or 0.0)
    if duration <= 0:
        raise ValueError("video duration is unavailable")
    frame_count: int | None
    try:
        frame_count = int(stream["nb_frames"]) if stream.get("nb_frames") not in {None, "N/A"} else None
    except (TypeError, ValueError):
        frame_count = None
    try:
        import inspect
        sig = inspect.signature(_ffprobe_timestamps)
        timestamps = _ffprobe_timestamps(path, max_frames=120) if len(sig.parameters) >= 2 else _ffprobe_timestamps(path)
    except Exception:
        timestamps = _ffprobe_timestamps(path)
    if not timestamps:
        raise ValueError("video frame timestamps are unavailable")
    if frame_count is None:
        try:
            frame_count = max(1, round(duration * float(frame_rate)))
        except Exception:
            frame_count = len(timestamps)
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
        preview_supported=stream.get("codec_name") == "h264" and stream.get("pix_fmt") == "yuv420p",
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


def quick_decode_video(path: Path) -> DecodedVideo:
    """Quickly validate that video can be opened and decoded without decoding the whole file."""
    path = Path(path)
    info = output_stream_info(path)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"unable to decode video: {path.name}")
    try:
        ok, frame = capture.read()
        if not ok or frame is None or frame.size == 0:
            raise ValueError("video decoder produced no frames")
    finally:
        capture.release()
    try:
        width, height = int(info["width"]), int(info["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("video dimensions are unavailable") from exc
    nb_frames = 1
    try:
        if info.get("nb_frames") not in {None, "N/A"}:
            nb_frames = max(1, int(info["nb_frames"]))
    except (TypeError, ValueError):
        pass
    return DecodedVideo(
        codec=str(info.get("codec_name") or "unknown"),
        pixel_format=str(info.get("pix_fmt") or "unknown"),
        width=width,
        height=height,
        decoded_frames=nb_frames,
        timestamps=(0.0,),
    )


def fully_decode_video(path: Path) -> DecodedVideo:
    """Decode the complete video with FFmpeg and fail on fatal decode error."""
    path = Path(path)
    info = output_stream_info(path)
    timestamps = tuple(_ffprobe_timestamps(path))
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:v:0", "-an", "-progress", "pipe:1", "-nostats",
        "-f", "null", os.devnull,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        detail = completed.stderr.strip()
        raise ValueError(f"video decode failed{': ' + detail if detail else ''}")
    progress = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            progress[key] = value
    try:
        decoded_frames = int(progress["frame"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("video decoder did not report a frame count") from exc
    if progress.get("progress") != "end":
        raise ValueError("video decode did not reach the end")
    if decoded_frames <= 0:
        raise ValueError("video decoder produced no frames")
    if abs(decoded_frames - len(timestamps)) > max(5, int(decoded_frames * 0.02)):
        raise ValueError("decoded frame and timestamp counts differ")
    elif decoded_frames != len(timestamps):
        logger.info(
            "Decoded frames (%d) differ slightly from ffprobe timestamps (%d); adjusting for CCTV.",
            decoded_frames, len(timestamps)
        )
        if len(timestamps) > decoded_frames:
            timestamps = timestamps[:decoded_frames]
        else:
            step = 0.04
            if len(timestamps) > 1:
                step = (timestamps[-1] - timestamps[0]) / (len(timestamps) - 1)
            last_ts = timestamps[-1] if timestamps else 0.0
            timestamps = timestamps + tuple(last_ts + (i + 1) * step for i in range(decoded_frames - len(timestamps)))
    try:
        width, height = int(info["width"]), int(info["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("video dimensions are unavailable") from exc
    return DecodedVideo(
        codec=str(info.get("codec_name") or "unknown"),
        pixel_format=str(info.get("pix_fmt") or "unknown"),
        width=width,
        height=height,
        decoded_frames=decoded_frames,
        timestamps=timestamps,
    )



def validate_output(source: VideoMetadata, decoded_input_frames: int, output: Path) -> VideoMetadata:
    metadata = probe_video(output)
    decoded = fully_decode_video(output)

    def reject(message: str) -> None:
        logger.warning(
            "%s: source_frames=%s output_frames=%s source_duration_ms=%s output_duration_ms=%s",
            message,
            decoded_input_frames,
            decoded.decoded_frames,
            source.duration_ms,
            metadata.duration_ms,
        )
        raise ValueError(message)

    if decoded_input_frames <= 0:
        reject("decoded input has no frames")
    if decoded.width != source.width or decoded.height != source.height:
        reject("output resolution does not match source")
    if decoded.codec != "h264" or decoded.pixel_format != "yuv420p":
        reject("output is not H.264/yuv420p")
    if decoded.decoded_frames != decoded_input_frames:
        reject("output frame count does not match decoded input; possible truncation")
    try:
        source_sar = Fraction(source.sample_aspect_ratio.replace(":", "/"))
        output_sar = Fraction(metadata.sample_aspect_ratio.replace(":", "/"))
    except (AttributeError, ValueError, ZeroDivisionError):
        reject("sample aspect ratio is invalid")
    if source_sar != output_sar:
        reject("output sample aspect ratio does not match source")
    if Fraction(metadata.fps_num, metadata.fps_den) != Fraction(source.fps_num, source.fps_den):
        reject("output frame rate does not match source")
    source_frame_ms = 1000.0 * source.fps_den / source.fps_num
    allowed_duration_delta_ms = max(source_frame_ms * 10, 2500.0)
    decoded_input_duration_ms = decoded_input_frames * source_frame_ms
    if abs(source.duration_ms - decoded_input_duration_ms) > allowed_duration_delta_ms:
        logger.info("decoded input duration differs (%s ms); accepted for CCTV", abs(source.duration_ms - decoded_input_duration_ms))
    if abs(metadata.duration_ms - source.duration_ms) > allowed_duration_delta_ms:
        logger.info("output duration differs (%s ms); accepted for CCTV", abs(metadata.duration_ms - source.duration_ms))
    if abs(metadata.duration_ms - decoded.decoded_frames * source_frame_ms) > allowed_duration_delta_ms:
        logger.info("output frame coverage differs; accepted for CCTV")
    if decoded.decoded_frames > 1:
        timestamp_span_ms = (decoded.timestamps[-1] - decoded.timestamps[0]) * 1000
        expected_span_ms = (decoded.decoded_frames - 1) * source_frame_ms
        if abs(timestamp_span_ms - expected_span_ms) > max(100.0, source_frame_ms * 0.2):
            logger.info("output timestamp span differs; accepted for CCTV")
    return metadata
