"""Conservative live inference worker for supported pretrained classes only."""

from dataclasses import dataclass
import os
import threading
import time
from collections import deque

from app.rules.events import derive_events
from app.rules.transactions import correlate
from app.schemas import Observation, RuleConfig


@dataclass
class InferenceStatus:
    state: str = "not_configured"
    people_count: int = 0
    updated_at_ms: int | None = None
    candidates: dict[str, int] | None = None
    event_kinds: list[str] | None = None
    transaction_statuses: list[str] | None = None
    detections: list[dict] | None = None


class PersonInferenceWorker:
    """Runs YOLO-World with camera-specific candidate prompts.

    Candidate labels are never elevated to transaction evidence without validation.
    """
    CANDIDATE_CLASSES = ["person", "human hand", "banknote", "cash basket", "goods"]

    def __init__(self, camera_id: str, rtsp_env_key: str, interval_seconds: float = 0.35):
        self.camera_id, self.rtsp_env_key, self.interval_seconds = camera_id, rtsp_env_key, interval_seconds
        self.status = InferenceStatus()
        self._observations: deque[Observation] = deque(maxlen=180)
        self._detection_history: deque[tuple[int, list[dict]]] = deque(maxlen=120)
        self._rules = RuleConfig()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not os.environ.get(self.rtsp_env_key):
            self.status.state = "not_configured"
            return
        self.status.state = "loading_model"
        self._thread = threading.Thread(target=self._run, name=f"inference-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def snapshot(self) -> dict:
        delayed_detections = self._detections_for_hls()
        return {"state": self.status.state, "people_count": self.status.people_count, "updated_at_ms": self.status.updated_at_ms,
                "candidates": self.status.candidates or {}, "event_kinds": self.status.event_kinds or [],
                "transaction_statuses": self.status.transaction_statuses or [], "detections": delayed_detections,
                "scope": "candidate_detection_only"}

    def _run(self) -> None:
        try:
            import cv2
            from ultralytics import YOLOWorld
            model = YOLOWorld("yolov8s-worldv2.pt")
            model.set_classes(self.CANDIDATE_CLASSES)
            capture = cv2.VideoCapture(os.environ[self.rtsp_env_key])
            if not capture.isOpened():
                self.status.state = "connection_failed"
                return
            self.status.state = "running"
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    self.status.state = "connection_failed"
                    time.sleep(self.interval_seconds)
                    continue
                # Ultralytics ByteTrack preserves IDs across motion and occlusion.
                result = model.track(frame, persist=True, tracker="app/vision/bytetrack_retail.yaml", verbose=False,
                                     conf=0.45, iou=0.45, agnostic_nms=True, max_det=20)[0]
                names = result.names
                counts = {label: 0 for label in self.CANDIDATE_CLASSES}
                for class_id in result.boxes.cls.tolist():
                    label = names[int(class_id)]
                    counts[label] = counts.get(label, 0) + 1
                self.status.candidates = counts
                self.status.people_count = counts.get("person", 0)
                self.status.updated_at_ms = int(time.time() * 1000)
                observations = _suppress_overlaps(self._observations_from_result(result, names, frame.shape[1], frame.shape[0]))
                self.status.detections = [{"track_id": item.track_id, "kind": item.kind, "confidence": round(item.confidence, 2),
                    "bbox": item.bbox} for item in observations if item.bbox]
                self._detection_history.append((int(time.time() * 1000), self.status.detections))
                self._observations.extend(observations)
                events = derive_events(tuple(self._observations), self._rules)
                transactions = correlate(events, self._rules)
                self.status.event_kinds = [event.kind.value for event in events[-5:]]
                # A one-camera candidate remains evidence-only; it cannot trigger review.
                self.status.transaction_statuses = [transaction.status.value for transaction in transactions[-3:]]
                self.status.state = "running"
                self._stop.wait(self.interval_seconds)
            capture.release()
        except Exception:
            # Do not expose stream URLs, credentials, or model internals via the API.
            self.status.state = "model_or_stream_unavailable"

    def _detections_for_hls(self) -> list[dict]:
        """Return boxes from the video time currently being played, not latest RTSP time."""
        if not self._detection_history:
            return []
        target = int(time.time() * 1000) - 3_500
        return min(self._detection_history, key=lambda entry: abs(entry[0] - target))[1]

    def _observations_from_result(self, result, names, width: int, height: int) -> list[Observation]:
        label_map = {"person": "person", "human hand": "hand", "banknote": "cash_note", "cash basket": "cash_basket", "goods": "goods"}
        rows: list[tuple[str, float, tuple[float, float, float, float]]] = []
        track_ids = result.boxes.id.tolist() if result.boxes.id is not None else [None] * len(result.boxes)
        for box, class_id, confidence, track_id in zip(result.boxes.xyxy.tolist(), result.boxes.cls.tolist(), result.boxes.conf.tolist(), track_ids):
            label = names[int(class_id)]
            kind = label_map.get(label)
            if not kind:
                continue
            x1, y1, x2, y2 = box
            bbox = (max(0.0, x1 / width), max(0.0, y1 / height), min(1.0, x2 / width), min(1.0, y2 / height))
            if bbox[0] < bbox[2] and bbox[1] < bbox[3]:
                rows.append((kind, confidence, bbox, track_id))
        basket_boxes = [bbox for kind, _, bbox, _ in rows if kind == "cash_basket"]
        timestamp = int(time.time() * 1000)
        observations = []
        for kind, confidence, bbox, track_id in rows:
            center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            roi = "cash_basket" if kind == "cash_basket" or (kind == "cash_note" and any(_contains(box, center) for box in basket_boxes)) else None
            stable_id = f"{self.camera_id}-{int(track_id)}" if track_id is not None else None
            observations.append(Observation(camera_id=self.camera_id, timestamp_ms=timestamp, kind=kind, confidence=confidence, bbox=bbox, roi_id=roi, track_id=stable_id))
        return observations


def _contains(bbox: tuple[float, float, float, float], point: tuple[float, float]) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _suppress_overlaps(observations: list[Observation], threshold: float = 0.35) -> list[Observation]:
    """A final display guard: one overlapping box per class/object candidate."""
    accepted: list[Observation] = []
    for observation in sorted(observations, key=lambda item: item.confidence, reverse=True):
        if observation.bbox and any(item.kind == observation.kind and item.bbox and _iou(item.bbox, observation.bbox) >= threshold for item in accepted):
            continue
        accepted.append(observation)
    return accepted


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    left, top, right, bottom = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    if not intersection:
        return 0.0
    area_a, area_b = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)
