from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import Observation, RunConfig
from .media import SampledFrame


class MediaPipeHandDetector:
    def __init__(
        self,
        model_path: Path,
        config: RunConfig,
        *,
        sdk: Any | None = None,
        landmarker: Any | None = None,
    ) -> None:
        if sdk is None:
            import mediapipe as sdk  # type: ignore[no-redef]

        self._sdk = sdk
        self._closed = False
        if landmarker is None:
            options = sdk.tasks.vision.HandLandmarkerOptions(
                base_options=sdk.tasks.BaseOptions(model_asset_path=str(model_path)),
                running_mode=sdk.tasks.vision.RunningMode.VIDEO,
                num_hands=config.max_hands,
                min_hand_detection_confidence=config.mp_detection_threshold,
                min_hand_presence_confidence=config.mp_presence_threshold,
                min_tracking_confidence=config.mp_tracking_threshold,
            )
            landmarker = sdk.tasks.vision.HandLandmarker.create_from_options(options)
        self._landmarker = landmarker

    def detect(self, frame: SampledFrame) -> list[Observation]:
        if self._closed:
            raise RuntimeError("MediaPipe detector is closed")
        rgb = np.ascontiguousarray(frame.rgb, dtype=np.uint8)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("MediaPipe input must be an RGB image")
        image = self._sdk.Image(image_format=self._sdk.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, frame.timestamp_ms)
        hands = list(result.hand_landmarks or [])
        handedness = list(result.handedness or [])
        observations: list[Observation] = []
        for index, landmarks in enumerate(hands):
            if not landmarks:
                raise ValueError("MediaPipe returned an empty landmark set")
            xs = [float(landmark.x) for landmark in landmarks]
            ys = [float(landmark.y) for landmark in landmarks]
            if not all(math.isfinite(v) and 0 <= v <= 1 for v in (*xs, *ys)):
                raise ValueError("MediaPipe returned a malformed landmark")
            source_min = frame.transform.source_xy(min(xs), min(ys))
            source_max = frame.transform.source_xy(max(xs), max(ys))
            if source_min == source_max:
                raise ValueError("MediaPipe returned a zero-area landmark box")

            score: float | None = None
            score_kind = "unavailable"
            if index < len(handedness) and handedness[index]:
                score = float(handedness[index][0].score)
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("MediaPipe returned malformed handedness")
                score_kind = "handedness"
            observations.append(
                Observation(
                    frame_index=frame.source_index,
                    bbox=(*source_min, *source_max),
                    score=score,
                    score_kind=score_kind,
                )
            )
        return observations

    def close(self) -> None:
        if not self._closed:
            self._landmarker.close()
            self._closed = True
