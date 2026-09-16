"""MediaPipe 21-Keypoint Hand Analyzer and Vietnamese Currency Detector for CCTV AI."""

import os
import math
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Connections for 21-keypoint MediaPipe hand
# 0: Wrist
# 1-4: Thumb (CMC, MCP, IP, TIP)
# 5-8: Index (MCP, PIP, DIP, TIP)
# 9-12: Middle (MCP, PIP, DIP, TIP)
# 13-16: Ring (MCP, PIP, DIP, TIP)
# 17-20: Pinky (MCP, PIP, DIP, TIP)
HAND_PALM_CONNECTIONS = [(0, 1), (1, 2), (2, 5), (5, 9), (9, 13), (13, 17), (0, 17)]
HAND_FINGER_CONNECTIONS = {
    "thumb": [(2, 3), (3, 4)],
    "index": [(5, 6), (6, 7), (7, 8)],
    "middle": [(9, 10), (10, 11), (11, 12)],
    "ring": [(13, 14), (14, 15), (15, 16)],
    "pinky": [(17, 18), (18, 19), (19, 20)],
}

FINGER_COLORS = {
    "thumb": (255, 230, 0),     # Neon Cyan
    "index": (80, 255, 100),    # Neon Emerald Green
    "middle": (0, 215, 255),    # Neon Gold / Amber
    "ring": (255, 50, 200),     # Neon Rose / Magenta
    "pinky": (255, 100, 180),   # Neon Purple / Violet
    "palm": (200, 200, 200),    # Pale Slate
}


@dataclass
class HandAnalysisResult:
    detected: bool = False
    landmarks: List[Tuple[float, float, float]] = field(default_factory=list)  # 21 normalized coords (x, y, z)
    gesture: str = "None"
    gesture_score: float = 0.0
    is_pinching: bool = False
    pinch_distance: float = 1.0  # Normalized pinch distance (thumb tip to index tip)
    is_fist: bool = False
    is_open: bool = False
    handedness: str = "Unknown"  # "Left" or "Right"


class MediaPipeHandAnalyzer:
    """Fast 21-point 3D hand landmarker & gesture recognizer operating on basket ROI crops."""

    def __init__(self, model_path: Optional[str] = None):
        self._recognizer = None
        self._initialized = False
        self._model_path = model_path or self._resolve_model_path()
        self._last_result = HandAnalysisResult()

    def _resolve_model_path(self) -> str:
        candidates = [
            Path("backend/weights/gesture_recognizer.task").resolve(),
            Path("weights/gesture_recognizer.task").resolve(),
            Path("backend/weights/hand_landmarker.task").resolve(),
            Path("weights/hand_landmarker.task").resolve(),
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return "backend/weights/gesture_recognizer.task"

    def _ensure_recognizer(self):
        if self._initialized:
            return self._recognizer

        self._initialized = True
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            resolved = Path(self._model_path).resolve()
            if not resolved.exists():
                # Try relative to current file or workspace
                alt = Path(__file__).parents[2] / "weights" / "gesture_recognizer.task"
                if alt.exists():
                    resolved = alt
                else:
                    logger.warning("MediaPipe task model not found at %s", self._model_path)
                    return None

            base_options = python.BaseOptions(model_asset_path=str(resolved))
            options = vision.GestureRecognizerOptions(
                base_options=base_options,
                num_hands=2,
                min_hand_detection_confidence=0.25,
                min_hand_presence_confidence=0.25,
                min_tracking_confidence=0.25,
            )
            self._recognizer = vision.GestureRecognizer.create_from_options(options)
            logger.info("MediaPipe GestureRecognizer initialized successfully from %s", resolved)
        except Exception as exc:
            logger.warning("Failed to initialize MediaPipe GestureRecognizer: %s", exc)
            self._recognizer = None

        return self._recognizer

    def analyze(self, crop_bgr: np.ndarray) -> HandAnalysisResult:
        """Analyzes a cropped bounding box around the basket ROI for 21-point hand landmarks & gestures."""
        if crop_bgr is None or crop_bgr.size == 0 or crop_bgr.shape[0] < 15 or crop_bgr.shape[1] < 15:
            return HandAnalysisResult()

        recognizer = self._ensure_recognizer()
        if recognizer is None:
            return HandAnalysisResult()

        try:
            import mediapipe as mp

            # MediaPipe tasks require RGB uint8 format
            crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=crop_rgb)

            result = recognizer.recognize(mp_image)
            if not result.hand_landmarks or len(result.hand_landmarks) == 0:
                self._last_result = HandAnalysisResult()
                return self._last_result

            # Primary hand
            hand_lms = result.hand_landmarks[0]
            landmarks = [(lm.x, lm.y, lm.z) for lm in hand_lms]

            top_gesture = "None"
            top_score = 0.0
            if result.gestures and len(result.gestures) > 0 and len(result.gestures[0]) > 0:
                g = result.gestures[0][0]
                top_gesture = g.category_name
                top_score = float(g.score)

            handedness = "Unknown"
            if result.handedness and len(result.handedness) > 0 and len(result.handedness[0]) > 0:
                handedness = result.handedness[0][0].category_name

            # Calculate Pinch Metric: Distance between Thumb Tip (4) and Index Tip (8)
            # Normalized against Palm Reference Distance (Wrist 0 to Middle MCP 9)
            thumb_tip = landmarks[4]
            index_tip = landmarks[8]
            wrist = landmarks[0]
            middle_mcp = landmarks[9]

            palm_ref = math.hypot(wrist[0] - middle_mcp[0], wrist[1] - middle_mcp[1]) + 1e-6
            pinch_dist_norm = math.hypot(thumb_tip[0] - index_tip[0], thumb_tip[1] - index_tip[1]) / palm_ref

            # Pinch threshold: thumb and index tips are pinched together (< 0.45 of palm length)
            is_pinching = (pinch_dist_norm < 0.45)

            # Detect Fist (curled fingers) vs Open Palm
            # Middle tip (12), Ring tip (16), Pinky tip (20) distance to wrist
            middle_tip = landmarks[12]
            ring_tip = landmarks[16]
            pinky_tip = landmarks[20]

            fingers_curl = (
                math.hypot(middle_tip[0] - wrist[0], middle_tip[1] - wrist[1]) +
                math.hypot(ring_tip[0] - wrist[0], ring_tip[1] - wrist[1]) +
                math.hypot(pinky_tip[0] - wrist[0], pinky_tip[1] - wrist[1])
            ) / (3.0 * palm_ref)

            is_fist = (top_gesture == "Closed_Fist") or (fingers_curl < 1.1)
            is_open = (top_gesture == "Open_Palm") or (fingers_curl > 1.6 and not is_pinching)

            if is_pinching and top_gesture in ("None", ""):
                top_gesture = "Pinch"

            self._last_result = HandAnalysisResult(
                detected=True,
                landmarks=landmarks,
                gesture=top_gesture,
                gesture_score=top_score,
                is_pinching=is_pinching,
                pinch_distance=pinch_dist_norm,
                is_fist=is_fist,
                is_open=is_open,
                handedness=handedness,
            )
            return self._last_result
        except Exception as exc:
            logger.debug("MediaPipe hand analysis exception: %s", exc)
            return HandAnalysisResult()


class VietnameseCurrencyDetector:
    """Detects Vietnamese banknotes (10k, 20k, 50k, 100k, 200k, 500k VNĐ) in cash basket ROI."""

    DENOMINATION_MAP = {
        "1000": "1.000đ",
        "2000": "2.000đ",
        "5000": "5.000đ",
        "10000": "10.000đ",
        "20000": "20.000đ",
        "50000": "50.000đ",
        "100000": "100.000đ",
        "200000": "200.000đ",
        "500000": "500.000đ",
    }

    def __init__(self, model_path: Optional[str] = None):
        self._model = None
        self._initialized = False
        self._model_path = model_path or "backend/weights/vietnamese_currency_yolo.pt"
        self.last_detected_label: Optional[str] = None
        self.last_confidence: float = 0.0

    def _ensure_model(self):
        if self._initialized:
            return self._model
        self._initialized = True
        try:
            resolved = Path(self._model_path).resolve()
            if not resolved.exists():
                alt = Path(__file__).parents[2] / "weights" / "vietnamese_currency_yolo.pt"
                if alt.exists():
                    resolved = alt
                else:
                    logger.warning("Vietnamese currency model not found at %s", self._model_path)
                    return None
            from ultralytics import YOLO
            self._model = YOLO(str(resolved))
            logger.info("Loaded Vietnamese Currency YOLO model from %s", resolved)
        except Exception as exc:
            logger.warning("Failed to load Vietnamese Currency YOLO model: %s", exc)
            self._model = None
        return self._model

    def detect_currency(self, crop_bgr: np.ndarray) -> Tuple[Optional[str], float, Optional[Tuple[int, int, int, int]]]:
        """Runs inference on basket crop to check if a banknote is visible."""
        if crop_bgr is None or crop_bgr.size == 0 or crop_bgr.shape[0] < 30 or crop_bgr.shape[1] < 30:
            return None, 0.0, None

        model = self._ensure_model()
        if model is None:
            return None, 0.0, None

        try:
            results = model.predict(crop_bgr, imgsz=224, conf=0.18, verbose=False, device="cpu")
            if not results or len(results) == 0:
                return None, 0.0, None

            res = results[0]
            if res.boxes is None or len(res.boxes) == 0:
                return None, 0.0, None

            best_conf = 0.0
            best_label = None
            best_box = None
            for box in res.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                raw_name = model.names.get(cls_id, str(cls_id))
                friendly_name = self.DENOMINATION_MAP.get(str(raw_name), f"{raw_name}đ")
                if conf > best_conf:
                    best_conf = conf
                    best_label = friendly_name
                    best_box = tuple(int(round(x)) for x in box.xyxy[0].tolist())

            if best_label and best_conf >= 0.18:
                self.last_detected_label = best_label
                self.last_confidence = best_conf
                return best_label, best_conf, best_box

            return None, 0.0, None
        except Exception as exc:
            logger.debug("Currency detection exception: %s", exc)
            return None, 0.0, None


def draw_detected_currency(
    scene: np.ndarray,
    crop_x1: int,
    crop_y1: int,
    label: str,
    confidence: float,
    box_crop: Tuple[int, int, int, int],
) -> np.ndarray:
    """Draws a high-visibility glowing bounding box and badge for the detected banknote."""
    if not box_crop or len(box_crop) < 4:
        return scene

    bx1 = crop_x1 + box_crop[0]
    by1 = crop_y1 + box_crop[1]
    bx2 = crop_x1 + box_crop[2]
    by2 = crop_y1 + box_crop[3]

    h_s, w_s = scene.shape[:2]
    bx1 = max(0, min(w_s - 1, bx1))
    by1 = max(0, min(h_s - 1, by1))
    bx2 = max(0, min(w_s - 1, bx2))
    by2 = max(0, min(h_s - 1, by2))

    color = (100, 255, 80)  # Emerald Neon
    cv2.rectangle(scene, (bx1, by1), (bx2, by2), color, 2, cv2.LINE_AA)

    badge_text = f"TIEN: {label} ({confidence * 100:.0f}%)"
    t_size, _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)

    tag_y = max(20, by1 - 6)
    cv2.rectangle(
        scene,
        (bx1 - 2, tag_y - t_size[1] - 4),
        (bx1 + t_size[0] + 6, tag_y + 4),
        (15, 23, 42),
        -1,
    )
    cv2.rectangle(
        scene,
        (bx1 - 2, tag_y - t_size[1] - 4),
        (bx1 + t_size[0] + 6, tag_y + 4),
        color,
        1,
    )
    cv2.putText(
        scene,
        badge_text,
        (bx1 + 2, tag_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return scene


def draw_cyber_hand_skeleton(
    scene: np.ndarray,
    landmarks: List[Tuple[float, float, float]],
    crop_x1: int,
    crop_y1: int,
    crop_w: int,
    crop_h: int,
    is_pinching: bool = False,
    gesture_name: str = "None",
    currency_label: Optional[str] = None,
) -> np.ndarray:
    """Renders 21-point high-tech cybernetic skeleton and status HUD on the scene."""
    if not landmarks or len(landmarks) < 21:
        return scene

    h_scene, w_scene = scene.shape[:2]

    # Convert normalized landmarks [0, 1] relative to crop into absolute pixel coordinates
    pts = []
    for lm in landmarks:
        px = int(round(crop_x1 + lm[0] * crop_w))
        py = int(round(crop_y1 + lm[1] * crop_h))
        px = max(0, min(w_scene - 1, px))
        py = max(0, min(h_scene - 1, py))
        pts.append((px, py))

    # 1. Draw Palm Base Connections
    palm_color = FINGER_COLORS["palm"]
    for i1, i2 in HAND_PALM_CONNECTIONS:
        cv2.line(scene, pts[i1], pts[i2], palm_color, 2, cv2.LINE_AA)

    # 2. Draw 5 Fingers Connections
    for finger_name, conns in HAND_FINGER_CONNECTIONS.items():
        color = FINGER_COLORS.get(finger_name, (0, 255, 255))
        for i1, i2 in conns:
            cv2.line(scene, pts[i1], pts[i2], color, 2, cv2.LINE_AA)

    # 3. If Pinching: Draw glowing pulsing line between Thumb Tip (4) and Index Tip (8)
    if is_pinching:
        cv2.line(scene, pts[4], pts[8], (0, 0, 255), 3, cv2.LINE_AA)
        cv2.line(scene, pts[4], pts[8], (100, 100, 255), 1, cv2.LINE_AA)
        # Draw pinch pulse circle
        mid_pinch = ((pts[4][0] + pts[8][0]) // 2, (pts[4][1] + pts[8][1]) // 2)
        cv2.circle(scene, mid_pinch, 6, (0, 0, 255), -1, cv2.LINE_AA)
        cv2.circle(scene, mid_pinch, 9, (150, 150, 255), 1, cv2.LINE_AA)

    # 4. Draw 21 Joint Dots
    for idx, pt in enumerate(pts):
        is_tip = idx in (4, 8, 12, 16, 20)
        radius = 4 if is_tip else 3
        # Tip joints glow brighter
        dot_color = (0, 255, 255) if is_tip else (255, 255, 255)
        if idx == 4 or idx == 8:
            if is_pinching:
                dot_color = (0, 0, 255)  # Red pinch tips
        cv2.circle(scene, pt, radius + 1, (15, 23, 42), -1, cv2.LINE_AA)
        cv2.circle(scene, pt, radius, dot_color, -1, cv2.LINE_AA)

    # 5. Render HUD Badge above the wrist or thumb
    hud_x = max(10, min(w_scene - 180, pts[0][0] - 40))
    hud_y = max(20, pts[0][1] - 25)

    badge_text = "TAY: "
    if is_pinching:
        badge_text += "NHON TIEN (PINCH)"
        badge_bg = (30, 30, 200)  # Red
    elif gesture_name == "Closed_Fist":
        badge_text += "NAM TAY (FIST)"
        badge_bg = (20, 100, 200)
    elif gesture_name == "Open_Palm":
        badge_text += "XOE TAY (OPEN)"
        badge_bg = (50, 160, 50)
    else:
        badge_text += "21-DIEM MEDIAPIPE"
        badge_bg = (20, 20, 30)

    if currency_label:
        badge_text += f" | {currency_label}"

    text_size, _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
    cv2.rectangle(
        scene,
        (hud_x - 3, hud_y - text_size[1] - 4),
        (hud_x + text_size[0] + 5, hud_y + 4),
        badge_bg,
        -1,
    )
    cv2.rectangle(
        scene,
        (hud_x - 3, hud_y - text_size[1] - 4),
        (hud_x + text_size[0] + 5, hud_y + 4),
        (0, 255, 255),
        1,
    )
    cv2.putText(
        scene,
        badge_text,
        (hud_x, hud_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    return scene
