"""Dedicated hand landmarks detector with lightweight, stable local IDs."""

from dataclasses import dataclass
from math import hypot
from pathlib import Path
from time import monotonic_ns


@dataclass(frozen=True)
class HandDetection:
    bbox: tuple[float, float, float, float]
    confidence: float
    track_id: str


class HandLandmarkTracker:
    """Uses MediaPipe's hand model and assigns IDs by nearby hand position.

    MediaPipe tracks landmarks internally between video frames, but does not expose
    an ID. This small association layer supplies a stable ID for the UI/events.
    """

    def __init__(self, camera_id: str, max_hands: int = 4) -> None:
        import mediapipe as mp

        self.camera_id = camera_id
        model_path = Path(__file__).resolve().parents[2] / "weights" / "hand_landmarker.task"
        if not model_path.is_file():
            raise FileNotFoundError("hand landmarker model is unavailable")
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=0.55,
            min_hand_presence_confidence=0.55,
            min_tracking_confidence=0.60,
        )
        self._mp = mp
        self._hands = mp.tasks.vision.HandLandmarker.create_from_options(options)
        self._last_timestamp_ms = 0
        self._tracks: dict[int, tuple[float, float, int]] = {}
        self._next_id = 1

    def detect(self, bgr_frame) -> list[HandDetection]:
        import cv2

        timestamp_ms = max(self._last_timestamp_ms + 1, monotonic_ns() // 1_000_000)
        self._last_timestamp_ms = timestamp_ms
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB),
        )
        result = self._hands.detect_for_video(image, timestamp_ms)
        candidates: list[tuple[tuple[float, float, float, float], float]] = []
        landmarks = result.hand_landmarks or []
        handedness = result.handedness or []
        for index, hand in enumerate(landmarks):
            xs, ys = [point.x for point in hand.landmark], [point.y for point in hand.landmark]
            padding = 0.025
            bbox = (max(0.0, min(xs) - padding), max(0.0, min(ys) - padding),
                    min(1.0, max(xs) + padding), min(1.0, max(ys) + padding))
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                continue
            score = 0.75
            if index < len(handedness) and handedness[index].classification:
                score = handedness[index].classification[0].score
            candidates.append((bbox, score))
        return self._associate(candidates)

    def close(self) -> None:
        self._hands.close()

    def _associate(self, candidates: list[tuple[tuple[float, float, float, float], float]]) -> list[HandDetection]:
        available = set(self._tracks)
        result: list[HandDetection] = []
        for bbox, confidence in candidates:
            center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            closest = min(available, key=lambda track: _distance(center, self._tracks[track][:2]), default=None)
            if closest is None or _distance(center, self._tracks[closest][:2]) > 0.18:
                closest, self._next_id = self._next_id, self._next_id + 1
            else:
                available.remove(closest)
            self._tracks[closest] = (center[0], center[1], 0)
            result.append(HandDetection(bbox=bbox, confidence=confidence, track_id=f"{self.camera_id}-hand-{closest}"))
        active = {item.track_id.rsplit("-", 1)[-1] for item in result}
        for track_id, (x, y, missed) in tuple(self._tracks.items()):
            if str(track_id) not in active:
                if missed >= 8:
                    del self._tracks[track_id]
                else:
                    self._tracks[track_id] = (x, y, missed + 1)
        return result


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])
