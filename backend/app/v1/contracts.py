from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    IMPORTED = "imported"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Stage(StrEnum):
    IMPORTED = "imported"
    QUEUED = "queued"
    LOADING = "loading"
    TRACKING = "tracking"
    ENCODING = "encoding"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class VideoMetadata:
    size_bytes: int
    width: int
    height: int
    duration_ms: int
    fps_num: int
    fps_den: int
    frame_count_estimate: int | None
    codec: str
    preview_supported: bool
    sample_aspect_ratio: str = "1:1"


@dataclass(frozen=True)
class TrackedPerson:
    track_id: int
    confidence: float
    xyxy: tuple[float, float, float, float]


@dataclass(frozen=True)
class FrameTracking:
    people: list[TrackedPerson]
    inference_ms: float | None
    tracking_wall_ms: float


@dataclass(frozen=True)
class RunOptions:
    model_path: str
    device: str = "auto"
    image_size: int = 960


@dataclass(frozen=True)
class Progress:
    stage: Stage
    processed_frames: int
    total_frames_estimate: int | None


@dataclass(frozen=True)
class RunSummary:
    actual_device: str
    device_name: str
    processed_frames: int
    local_track_count: int
    inference_samples: int
    mean_inference_ms: float | None
    tracking_wall_ms_total: float
    processing_seconds: float
    effective_fps: float
    output_duration_ms: int
