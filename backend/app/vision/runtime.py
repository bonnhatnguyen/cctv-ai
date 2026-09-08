"""Conservative live inference worker for supported pretrained classes only."""

from dataclasses import dataclass
import os
import threading
import time
from collections import deque

from app.rules.events import derive_events
from app.rules.transactions import correlate
from app.schemas import Observation, RuleConfig
from app.vision.tracker import Tracker


@dataclass
class InferenceStatus:
    state: str = "not_configured"
    people_count: int = 0
    updated_at_ms: int | None = None
    candidates: dict[str, int] | None = None
    event_kinds: list[str] | None = None
    transaction_statuses: list[str] | None = None


class PersonInferenceWorker:
    """Runs YOLO-World with camera-specific candidate prompts.

    Candidate labels are never elevated to transaction evidence without validation.
    """
    CANDIDATE_CLASSES = ["person", "human hand", "banknote", "cash basket", "goods"]

    def __init__(self, camera_id: str, rtsp_env_key: str, interval_seconds: float = 1.0):
        self.camera_id, self.rtsp_env_key, self.interval_seconds = camera_id, rtsp_env_key, interval_seconds
        self.status = InferenceStatus()
        self._tracker = Tracker()
        self._observations: deque[Observation] = deque(maxlen=180)
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
        return {"state": self.status.state, "people_count": self.status.people_count, "updated_at_ms": self.status.updated_at_ms,
                "candidates": self.status.candidates or {}, "event_kinds": self.status.event_kinds or [],
                "transaction_statuses": self.status.transaction_statuses or [], "scope": "candidate_detection_only"}

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
                result = model(frame, verbose=False, conf=0.20)[0]
                names = result.names
                counts = {label: 0 for label in self.CANDIDATE_CLASSES}
                for class_id in result.boxes.cls.tolist():
                    label = names[int(class_id)]
                    counts[label] = counts.get(label, 0) + 1
                self.status.candidates = counts
                self.status.people_count = counts.get("person", 0)
                self.status.updated_at_ms = int(time.time() * 1000)
                observations = self._observations_from_result(result, names, frame.shape[1], frame.shape[0])
                tracked = self._tracker.update(self.camera_id, observations)
                self._observations.extend(tracked)
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

    def _observations_from_result(self, result, names, width: int, height: int) -> list[Observation]:
        label_map = {"person": "person", "human hand": "hand", "banknote": "cash_note", "cash basket": "cash_basket", "goods": "goods"}
        rows: list[tuple[str, float, tuple[float, float, float, float]]] = []
        for box, class_id, confidence in zip(result.boxes.xyxy.tolist(), result.boxes.cls.tolist(), result.boxes.conf.tolist()):
            label = names[int(class_id)]
            kind = label_map.get(label)
            if not kind:
                continue
            x1, y1, x2, y2 = box
            bbox = (max(0.0, x1 / width), max(0.0, y1 / height), min(1.0, x2 / width), min(1.0, y2 / height))
            if bbox[0] < bbox[2] and bbox[1] < bbox[3]:
                rows.append((kind, confidence, bbox))
        basket_boxes = [bbox for kind, _, bbox in rows if kind == "cash_basket"]
        timestamp = int(time.time() * 1000)
        observations = []
        for kind, confidence, bbox in rows:
            center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            roi = "cash_basket" if kind == "cash_basket" or (kind == "cash_note" and any(_contains(box, center) for box in basket_boxes)) else None
            observations.append(Observation(camera_id=self.camera_id, timestamp_ms=timestamp, kind=kind, confidence=confidence, bbox=bbox, roi_id=roi))
        return observations


def _contains(bbox: tuple[float, float, float, float], point: tuple[float, float]) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]
