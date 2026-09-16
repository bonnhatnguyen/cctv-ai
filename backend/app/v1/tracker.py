from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .contracts import FrameTracking, TrackedPerson


def _resolve_device(device: str) -> str:
    if device != "auto":
        if device in {"cuda", "gpu"}:
            return "0"
        return device
    try:
        import torch

        return "0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, tuple):
        return list(value)
    return value if isinstance(value, list) else [value]


def _model_device(model: Any, result: Any, requested: str) -> str:
    predictor = getattr(model, "predictor", None)
    candidate = getattr(predictor, "device", None)
    boxes = getattr(result, "boxes", None)
    data = getattr(boxes, "data", None)
    candidate = candidate or getattr(data, "device", None) or getattr(model, "device", None)
    if candidate is None:
        candidate = requested
    text = str(candidate).lower()
    if text.startswith("cuda") or text == "gpu" or text.isdigit():
        return "cuda:" + (text.split(":", 1)[1] if ":" in text else "0")
    return "cpu"


class PersonTracker:
    """Thin adapter around Ultralytics' persistent official ByteTrack tracker."""

    def __init__(
        self,
        model_path: Path | str,
        device: str,
        image_size: int = 960,
        *,
        model_factory: Callable[[Path | str], Any] | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.is_file() and model_factory is None:
            raise FileNotFoundError(f"model not found: {self.model_path}")
        self.requested_device = device
        self.resolved_device = _resolve_device(device)
        self.image_size = image_size
        if model_factory is None:
            from ultralytics import YOLO

            model_factory = YOLO
        self._model = model_factory(self.model_path)
        self.actual_device: str | None = None
        self.device_name: str = ""

    def track_frame(self, frame: np.ndarray) -> FrameTracking:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3:
            raise ValueError("frame must be a color numpy array")
        started = time.perf_counter()
        results = self._model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            imgsz=self.image_size,
            device=self.resolved_device,
            classes=[0],
            verbose=False,
        )
        if isinstance(results, (list, tuple)):
            result = results[0] if results else None
        else:
            result = results
        if result is None:
            people: list[TrackedPerson] = []
        else:
            self.actual_device = _model_device(self._model, result, self.resolved_device)
            self.device_name = self._device_name(self.actual_device)
            people = self._people_from_result(result, frame.shape[1], frame.shape[0])
        wall_ms = (time.perf_counter() - started) * 1000.0
        return FrameTracking(people=people, inference_ms=self._inference_ms(result), tracking_wall_ms=wall_ms)

    @staticmethod
    def _device_name(actual_device: str) -> str:
        if not actual_device.startswith("cuda"):
            return "CPU"
        try:
            import torch

            index = int(actual_device.split(":", 1)[1])
            return torch.cuda.get_device_name(index)
        except Exception:
            return actual_device

    @staticmethod
    def _inference_ms(result: Any) -> float | None:
        speed = getattr(result, "speed", None)
        value = speed.get("inference") if isinstance(speed, dict) else None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value >= 0 else None

    @staticmethod
    def _people_from_result(result: Any, width: int, height: int) -> list[TrackedPerson]:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        coordinates = _as_list(getattr(boxes, "xyxy", None))
        classes = _as_list(getattr(boxes, "cls", None))
        confidences = _as_list(getattr(boxes, "conf", None))
        ids_value = getattr(boxes, "id", None)
        ids = _as_list(ids_value) if ids_value is not None else []
        people: list[TrackedPerson] = []
        for index, (box, class_id, confidence) in enumerate(zip(coordinates, classes, confidences)):
            try:
                if int(class_id) != 0:
                    continue
                track_id = ids[index] if index < len(ids) else None
                if track_id is None or not math.isfinite(float(track_id)):
                    continue
                values = tuple(float(value) for value in box)
                if len(values) != 4 or not all(math.isfinite(value) for value in values):
                    continue
                x1, y1, x2, y2 = values
                x1, x2 = max(0.0, min(float(width), x1)), max(0.0, min(float(width), x2))
                y1, y2 = max(0.0, min(float(height), y1)), max(0.0, min(float(height), y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                confidence_value = float(confidence)
                if not math.isfinite(confidence_value):
                    continue
                people.append(TrackedPerson(int(track_id), confidence_value, (x1, y1, x2, y2)))
            except (TypeError, ValueError, IndexError):
                continue
        return people
