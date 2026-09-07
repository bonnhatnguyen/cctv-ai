"""Custom-checkpoint detector adapter. It never falls back to generic COCO labels."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from app.schemas import Observation, RuleConfig
from app.video.rtsp import Frame

VALID_LABELS = frozenset({"person", "hand", "goods", "cash_note", "cash_stack", "cash_basket"})


class ModelUnavailableError(RuntimeError):
    """Neutral startup health failure when the required model cannot be loaded."""


@dataclass(frozen=True)
class RawDetection:
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]
    denomination: int | None = None
    denomination_confidence: float | None = None


class DetectionModel(Protocol):
    def predict(self, image) -> Iterable[RawDetection]: ...


def checkpoint_exists(path: str | Path) -> None:
    if not Path(path).is_file():
        raise ModelUnavailableError("model_unavailable")


class Detector:
    def __init__(self, model: DetectionModel, rois: dict[str, tuple[tuple[float, float], ...]] | None = None,
                 rules: RuleConfig | None = None):
        self.model, self.rois, self.rules = model, rois or {}, rules or RuleConfig()

    def detect(self, frame: Frame) -> list[Observation]:
        observations: list[Observation] = []
        for detection in self.model.predict(frame.image):
            if detection.label not in VALID_LABELS or not 0 <= detection.confidence <= 1:
                continue
            bbox = _normalized(detection.bbox)
            if bbox is None:
                continue
            denomination = detection.denomination
            if detection.denomination_confidence is None or detection.denomination_confidence < self.rules.denomination_confidence:
                denomination = None
            center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            observations.append(Observation(camera_id=frame.camera_id, timestamp_ms=frame.timestamp_ms,
                kind=detection.label, confidence=detection.confidence, bbox=bbox,
                roi_id=_roi_for(center, self.rois), denomination=denomination))
        return observations


def _normalized(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    return bbox


def _roi_for(point: tuple[float, float], rois: dict[str, tuple[tuple[float, float], ...]]) -> str | None:
    for name, polygon in rois.items():
        if _point_in_polygon(point, polygon):
            return name
    return None


def _point_in_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[index - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside
