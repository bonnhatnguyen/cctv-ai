"""Conservative live inference worker for supported pretrained classes only."""

from dataclasses import dataclass
import os
import threading
import time


@dataclass
class InferenceStatus:
    state: str = "not_configured"
    people_count: int = 0
    updated_at_ms: int | None = None
    candidates: dict[str, int] | None = None


class PersonInferenceWorker:
    """Runs YOLO-World with camera-specific candidate prompts.

    Candidate labels are never elevated to transaction evidence without validation.
    """
    CANDIDATE_CLASSES = ["person", "human hand", "banknote", "cash basket", "goods"]

    def __init__(self, camera_id: str, rtsp_env_key: str, interval_seconds: float = 1.0):
        self.camera_id, self.rtsp_env_key, self.interval_seconds = camera_id, rtsp_env_key, interval_seconds
        self.status = InferenceStatus()
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
                "candidates": self.status.candidates or {}, "scope": "candidate_detection_only"}

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
                self.status.state = "running"
                self._stop.wait(self.interval_seconds)
            capture.release()
        except Exception:
            # Do not expose stream URLs, credentials, or model internals via the API.
            self.status.state = "model_or_stream_unavailable"
