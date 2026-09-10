from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import cv2

from .contracts import Progress, RunOptions, RunSummary, Stage, TrackedPerson, VideoMetadata
from .media import annotate_people, fully_decode_video, probe_video, validate_output
from .tracker import PersonTracker


logger = logging.getLogger(__name__)


def _noop(_progress: Progress) -> None:
    return None


def _display_correct(frame, metadata: VideoMetadata):
    try:
        sar_num, sar_den = (int(part) for part in metadata.sample_aspect_ratio.split(":", 1))
    except (ValueError, TypeError):
        sar_num, sar_den = 1, 1
    if sar_num <= 0 or sar_den <= 0 or sar_num == sar_den:
        return frame
    display_width = round(frame.shape[1] * sar_num / sar_den)
    return cv2.resize(frame, (display_width, frame.shape[0]), interpolation=cv2.INTER_LINEAR)


def _map_people_to_source(people: list[TrackedPerson], source_width: int, inference_width: int) -> list[TrackedPerson]:
    if inference_width == source_width:
        return people
    scale = source_width / inference_width
    mapped: list[TrackedPerson] = []
    for person in people:
        x1, y1, x2, y2 = person.xyxy
        mapped.append(TrackedPerson(person.track_id, person.confidence, (x1 * scale, y1, x2 * scale, y2)))
    return mapped


def _start_encoder(output: Path, metadata: VideoMetadata):
    sar = metadata.sample_aspect_ratio.replace(":", "/")
    command = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s:v", f"{metadata.width}x{metadata.height}", "-r", f"{metadata.fps_num}/{metadata.fps_den}",
        "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf",
        f"setsar={sar}", "-movflags", "+faststart", str(output),
    ]
    return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return left.resolve(strict=False) == right.resolve(strict=False)


def _validate_paths(source: Path, output: Path, evidence: Path) -> None:
    if _same_file(source, output):
        raise ValueError("input, output, and evidence must be different files")
    if _same_file(source, evidence):
        raise ValueError("input, output, and evidence must be different files")
    if _same_file(output, evidence):
        raise ValueError("input, output, and evidence must be different files")
    if output.exists():
        raise FileExistsError(output)
    if evidence.exists():
        raise FileExistsError(evidence)


def process_video(
    source: Path,
    output: Path,
    options: RunOptions,
    on_progress: Callable[[Progress], None] = _noop,
    *,
    evidence_path: Path | None = None,
) -> RunSummary:
    source, output = Path(source), Path(output)
    started = time.perf_counter()
    if not source.is_file():
        raise FileNotFoundError(source)
    evidence_path = evidence_path or output.with_name(output.stem + ".evidence.jsonl")
    evidence_path = Path(evidence_path)
    _validate_paths(source, output, evidence_path)
    source_metadata = probe_video(source)
    source_decode = fully_decode_video(source)
    source_timestamps = source_decode.timestamps
    if source_decode.decoded_frames == 0:
        raise ValueError("video contains no decodable frames")
    if source_metadata.frame_count_estimate is not None and source_decode.decoded_frames != source_metadata.frame_count_estimate:
        raise ValueError("video frame timestamp count is inconsistent")
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_handle = None
    tracker = None
    capture = None
    encoder = None
    stderr_chunks: list[bytes] = []
    stderr_thread: threading.Thread | None = None
    processed = 0
    inference_samples = 0
    inference_total = 0.0
    tracking_wall_total = 0.0
    track_ids: set[int] = set()
    succeeded = False
    try:
        evidence_handle = evidence_path.open("x", encoding="utf-8")
        on_progress(Progress(Stage.LOADING, 0, source_metadata.frame_count_estimate))
        tracker = PersonTracker(options.model_path, options.device, options.image_size)
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise ValueError(f"unable to decode video: {source.name}")
        encoder = _start_encoder(output, source_metadata)
        if encoder.stderr is not None:
            def drain_stderr() -> None:
                while True:
                    chunk = encoder.stderr.read(65536)
                    if not chunk:
                        return
                    stderr_chunks.append(chunk)

            stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
            stderr_thread.start()
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            inference_frame = _display_correct(frame, source_metadata)
            tracking = tracker.track_frame(inference_frame)
            people = _map_people_to_source(tracking.people, source_metadata.width, inference_frame.shape[1])
            annotated = annotate_people(frame, people)
            assert encoder.stdin is not None
            try:
                encoder.stdin.write(annotated.tobytes())
            except (BrokenPipeError, OSError) as exc:
                encoder.wait()
                if stderr_thread:
                    stderr_thread.join(timeout=10)
                detail = b"".join(stderr_chunks).decode(errors="replace").strip()
                raise RuntimeError(f"video encoder failed{': ' + detail if detail else ''}") from exc
            processed += 1
            tracking_wall_total += tracking.tracking_wall_ms
            if tracking.inference_ms is not None:
                inference_samples += 1
                inference_total += tracking.inference_ms
            track_ids.update(person.track_id for person in people)
            if evidence_handle:
                evidence_handle.write(json.dumps({
                    "frame_index": processed - 1,
                    "source_timestamp_ms": round(source_timestamps[processed - 1] * 1000, 3),
                    "boxes": [{"track_id": p.track_id, "confidence": p.confidence, "xyxy": p.xyxy} for p in people],
                }, ensure_ascii=False) + "\n")
            on_progress(Progress(Stage.TRACKING, processed, source_metadata.frame_count_estimate))
        capture.release()
        capture = None
        if processed == 0:
            raise ValueError("video contains no decodable frames")
        if processed != source_decode.decoded_frames:
            logger.warning(
                "video decode ended early: expected_decoded_frames=%s processed_frames=%s",
                source_decode.decoded_frames,
                processed,
            )
            raise ValueError("video decode ended before the full source frame count; possible truncation")
        on_progress(Progress(Stage.ENCODING, processed, source_metadata.frame_count_estimate))
        assert encoder.stdin is not None
        try:
            encoder.stdin.close()
        except BrokenPipeError:
            pass
        return_code = encoder.wait()
        if stderr_thread:
            stderr_thread.join(timeout=10)
        if return_code:
            detail = b"".join(stderr_chunks).decode(errors="replace").strip()
            raise RuntimeError(f"video encoder failed{': ' + detail if detail else ''}")
        on_progress(Progress(Stage.VALIDATING, processed, source_metadata.frame_count_estimate))
        output_metadata = validate_output(source_metadata, processed, output)
        on_progress(Progress(Stage.READY, processed, source_metadata.frame_count_estimate))
        processing_seconds = time.perf_counter() - started
        actual_device = tracker.actual_device or tracker.resolved_device
        summary = RunSummary(
            actual_device=actual_device,
            device_name=tracker.device_name or ("CPU" if actual_device == "cpu" else actual_device),
            processed_frames=processed,
            local_track_count=len(track_ids),
            inference_samples=inference_samples,
            mean_inference_ms=(inference_total / inference_samples if inference_samples else None),
            tracking_wall_ms_total=tracking_wall_total,
            processing_seconds=processing_seconds,
            effective_fps=processed / processing_seconds if processing_seconds else math.inf,
            output_duration_ms=output_metadata.duration_ms,
        )
        succeeded = True
        return summary
    finally:
        if capture is not None:
            capture.release()
        if evidence_handle:
            evidence_handle.close()
        if encoder is not None and encoder.poll() is None:
            if encoder.stdin is not None:
                try:
                    encoder.stdin.close()
                except BrokenPipeError:
                    pass
            encoder.kill()
            encoder.wait()
        if stderr_thread:
            stderr_thread.join(timeout=2)
        # Failed runs must not leave a misleading partial result behind.
        if output.exists() and not succeeded:
            output.unlink(missing_ok=True)
