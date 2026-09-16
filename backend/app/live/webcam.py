"""Live webcam inference and MJPEG streaming service using YOLO and Roboflow Supervision."""

from collections import Counter
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Generator, Union

import cv2
import numpy as np
import supervision as sv

from .telegram import telegram_notifier
from .hand_analyzer import MediaPipeHandAnalyzer, VietnameseCurrencyDetector, draw_cyber_hand_skeleton, draw_detected_currency

logger = logging.getLogger(__name__)

# CCTV High-Contrast Cyberpunk Neon Palette
CCTV_PALETTE = sv.ColorPalette.from_hex([
    "#00E676",  # Emerald neon
    "#00E5FF",  # Cyan neon
    "#FFD600",  # Amber neon
    "#FF1744",  # Crimson neon
    "#D500F9",  # Magenta neon
    "#76FF03",  # Lime neon
    "#FF9100",  # Orange neon
])


def _resolve_pose_model_path(custom_path: Path | str | None = None) -> Path:
    if custom_path:
        p = Path(custom_path).resolve()
        if p.is_file():
            return p
    app_dir = Path(__file__).resolve().parent
    candidates = [
        app_dir.parents[2] / "backend" / "weights" / "yolo11n-pose.pt",
        app_dir.parents[2] / "weights" / "yolo11n-pose.pt",
        app_dir.parents[1] / "weights" / "yolo11n-pose.pt",
        Path("backend/weights/yolo11n-pose.pt").resolve(),
        Path("weights/yolo11n-pose.pt").resolve(),
        Path("yolo11n-pose.pt").resolve(),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return Path("yolo11n-pose.pt").resolve()


def _resolve_onnx_pose_model_path() -> Path:
    app_dir = Path(__file__).resolve().parent
    candidates = [
        app_dir.parents[2] / "backend" / "weights" / "yolo11n-pose.onnx",
        app_dir.parents[2] / "weights" / "yolo11n-pose.onnx",
        app_dir.parents[1] / "weights" / "yolo11n-pose.onnx",
        Path("backend/weights/yolo11n-pose.onnx").resolve(),
        Path("weights/yolo11n-pose.onnx").resolve(),
        Path("yolo11n-pose.onnx").resolve(),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return Path("yolo11n-pose.onnx").resolve()


def _resolve_model_path(custom_path: Path | str | None = None) -> Path:
    if custom_path:
        p = Path(custom_path).resolve()
        if p.is_file():
            return p

    env_path = os.environ.get("V1_TRACKING_MODEL_PATH")
    if env_path:
        p = Path(env_path).resolve()
        if p.is_file():
            return p

    # Probe project standard paths
    app_dir = Path(__file__).resolve().parent
    candidates = [
        app_dir.parents[2] / "backend" / "weights" / "yolo26n.pt",
        app_dir.parents[2] / "weights" / "yolo26n.pt",
        app_dir.parents[1] / "weights" / "yolo26n.pt",
        Path("weights/yolo26n.pt").resolve(),
        Path("backend/weights/yolo26n.pt").resolve(),
    ]
    for c in candidates:
        if c.is_file():
            return c

    # Fallback to local default
    return app_dir.parents[1] / "weights" / "yolo26n.pt"



def _resolve_config_path() -> Path:
    candidates = [
        Path("data/v1/live_config.json").resolve(),
        Path(__file__).resolve().parents[3] / "data" / "v1" / "live_config.json",
        Path(__file__).resolve().parents[2] / "data" / "v1" / "live_config.json",
    ]
    for c in candidates:
        if c.parent.exists():
            return c
    p = Path("data/v1/live_config.json").resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _resolve_evidence_dir() -> Path:
    candidates = [
        Path("data/v1/evidence").resolve(),
        Path(__file__).resolve().parents[3] / "data" / "v1" / "evidence",
        Path(__file__).resolve().parents[2] / "data" / "v1" / "evidence",
    ]
    for c in candidates:
        if c.parent.exists():
            c.mkdir(parents=True, exist_ok=True)
            return c
    p = Path("data/v1/evidence").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


class EvidenceManager:
    """Manages captured audit snapshots and metadata in data/v1/evidence/."""

    def __init__(self, storage_dir: Path | None = None, max_items: int = 100):
        self.storage_dir = storage_dir or _resolve_evidence_dir()
        self.max_items = max_items
        self._lock = threading.Lock()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_evidence(
        self,
        frame: np.ndarray,
        event_type: str,
        person_id: int | None = None,
        confidence: float = 0.0,
        duration_seconds: float = 0.0,
        summary: str = "",
    ) -> dict:
        with self._lock:
            now = datetime.now()
            ts_str = now.strftime("%Y%m%d_%H%M%S_%f")[:19]
            item_id = f"ev_{ts_str}"
            img_filename = f"{item_id}.jpg"
            json_filename = f"{item_id}.json"

            img_path = self.storage_dir / img_filename
            json_path = self.storage_dir / json_filename

            # Save JPEG with clean 88% quality
            cv2.imwrite(str(img_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])

            metadata = {
                "id": item_id,
                "filename": img_filename,
                "timestamp": now.isoformat(),
                "timestamp_ms": int(now.timestamp() * 1000),
                "event_type": event_type,
                "person_id": person_id,
                "confidence": round(float(confidence), 2),
                "duration_seconds": round(float(duration_seconds), 1),
                "image_url": f"/api/v1/live/webcam/evidence/{img_filename}",
                "summary": summary or f"Phát hiện sự kiện {event_type} tại rổ tiền",
            }

            json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self._cleanup_old()
            return metadata

    def list_evidence(self) -> list[dict]:
        with self._lock:
            items = []
            for jf in sorted(self.storage_dir.glob("ev_*.json"), reverse=True):
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    items.append(data)
                except Exception:
                    continue
            return items

    def get_image_path(self, filename: str) -> Path | None:
        p = (self.storage_dir / filename).resolve()
        if p.is_file() and p.parent == self.storage_dir.resolve():
            return p
        return None

    def clear_all(self) -> int:
        with self._lock:
            count = 0
            for f in self.storage_dir.glob("ev_*.*"):
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
            return count

    def count(self) -> int:
        with self._lock:
            return len(list(self.storage_dir.glob("ev_*.json")))

    def _cleanup_old(self) -> None:
        json_files = sorted(self.storage_dir.glob("ev_*.json"), key=lambda p: p.stat().st_mtime)
        if len(json_files) > self.max_items:
            to_remove = json_files[: len(json_files) - self.max_items]
            for jf in to_remove:
                try:
                    jf.unlink()
                    img_f = jf.with_suffix(".jpg")
                    if img_f.exists():
                        img_f.unlink()
                except Exception:
                    pass


class BasketState:
    IDLE = "IDLE"
    HAND_ENTERING = "HAND_ENTERING"
    CASH_INTERACTING = "CASH_INTERACTING"
    TRANSACTION_COMPLETE = "TRANSACTION_COMPLETE"


class CashBasketTracker:
    """Tracks hand-in-basket interactions using exact polygon masking, motion subtraction and skin heuristics."""

    def __init__(self, polygon: np.ndarray | list | None = None):
        if polygon is None:
            # Default cash basket ROI centered in lower half (640x480 resolution)
            self.base_points = [
                [135, 230],
                [215, 230],
                [220, 325],
                [130, 325],
            ]
        else:
            self.base_points = [list(p) for p in polygon]

        self.polygon = np.array(self.base_points, dtype=np.int32)
        self.current_frame_size = (480, 640)
        self.zone = sv.PolygonZone(polygon=self.polygon)
        self.state = BasketState.IDLE
        self.hand_in_basket = False
        self.active_person_id: int | None = None
        self.interaction_start_time: float = 0.0
        self.interaction_frames = 0
        self.last_snapshot_time: float = 0.0
        self.snapshot_cooldown = 2.5  # Min seconds between auto-snapshots

        # Dynamic reference background for the cropped cash basket ROI
        self.ref_background: np.ndarray | None = None
        self._mask_cache: tuple[tuple[int, int], np.ndarray] | None = None

        # Advanced MediaPipe 21-Keypoint Hand Tracking & Currency Detectors
        self.hand_analyzer = MediaPipeHandAnalyzer()
        self.currency_detector = VietnameseCurrencyDetector()
        self.last_landmarks: list | None = None
        self.last_gesture: str = "None"
        self.is_pinching: bool = False
        self.detected_currency: str | None = None
        self.detected_currency_conf: float = 0.0
        self.detected_currency_box: tuple[int, int, int, int] | None = None
        self.currency_history: list[tuple[str, float, tuple[int, int, int, int]]] = []
        self.last_crop_bbox: tuple[int, int, int, int] | None = None  # (x1, y1, w, h)
        self.interaction_pinch_occurred: bool = False
        self.interaction_fist_occurred: bool = False
        self.initial_pinch_or_fist: bool = False
        self.final_pinch_or_fist: bool = False

    def reset(self) -> None:
        self.state = BasketState.IDLE
        self.hand_in_basket = False
        self.active_person_id = None
        self.interaction_start_time = 0.0
        self.interaction_frames = 0
        self.ref_background = None
        self.last_landmarks = None
        self.last_gesture = "None"
        self.is_pinching = False
        self.detected_currency = None
        self.detected_currency_conf = 0.0
        self.detected_currency_box = None
        self.currency_history.clear()
        self.last_crop_bbox = None
        self.interaction_pinch_occurred = False
        self.interaction_fist_occurred = False
        self.initial_pinch_or_fist = False
        self.final_pinch_or_fist = False

    def ensure_frame_size(self, h_frame: int, w_frame: int) -> None:
        """Dynamically scales base ROI (640x480 reference space) to native frame dimensions."""
        if (h_frame, w_frame) == self.current_frame_size:
            return

        scale_x = w_frame / 640.0
        scale_y = h_frame / 480.0
        scaled = []
        for p in self.base_points:
            px = p[0] * w_frame if p[0] <= 1.0 else p[0] * scale_x
            py = p[1] * h_frame if p[1] <= 1.0 else p[1] * scale_y
            scaled.append([int(round(px)), int(round(py))])

        self.polygon = np.array(scaled, dtype=np.int32)
        self.zone = sv.PolygonZone(polygon=self.polygon)
        self.current_frame_size = (h_frame, w_frame)
        self._mask_cache = None
        self.ref_background = None

    def set_roi(self, points: list[list[int | float]]) -> None:
        self.base_points = [list(p) for p in points]
        self.current_frame_size = (0, 0)
        self.polygon = np.array(points, dtype=np.int32)
        self.zone = sv.PolygonZone(polygon=self.polygon)
        self.ref_background = None
        self._mask_cache = None
        self.reset()

    def process_frame(
        self,
        frame: np.ndarray,
        detections: sv.Detections,
        keypoints: any = None,
    ) -> tuple[str, dict | None]:
        """Processes frame & person detections, advancing the cash basket state machine."""
        h_frame, w_frame = frame.shape[:2]
        self.ensure_frame_size(h_frame, w_frame)

        # 1. Bounding box around the basket with padding
        bx, by, bw, bh = cv2.boundingRect(self.polygon)
        pad = 20
        x1 = max(0, bx - pad)
        y1 = max(0, by - pad)
        x2 = min(w_frame, bx + bw + pad)
        y2 = min(h_frame, by + bh + pad)
        crop_w = x2 - x1
        crop_h = y2 - y1

        if crop_w < 15 or crop_h < 15:
            return self.state, None

        # 2. Local crop and mask (25x faster than processing the whole 1MP frame)
        crop_frame = frame[y1:y2, x1:x2]
        self.last_crop_bbox = (x1, y1, crop_w, crop_h)
        local_poly = self.polygon - np.array([x1, y1], dtype=np.int32)
        crop_mask = np.zeros((crop_h, crop_w), dtype=np.uint8)
        cv2.fillPoly(crop_mask, [local_poly], 255)
        poly_area = cv2.countNonZero(crop_mask)

        if poly_area < 50:
            return self.state, None

        # 3. Check wrist keypoints from pose model (first-class detection)
        wrist_in_basket = False
        wrist_person_id = None
        wrist_conf = 0.0

        if keypoints is not None:
            try:
                kpts_xy = getattr(keypoints, "xy", keypoints)
                kpts_conf = getattr(keypoints, "conf", None)
                if hasattr(kpts_xy, "cpu"):
                    kpts_xy = kpts_xy.cpu().numpy()
                if kpts_conf is not None and hasattr(kpts_conf, "cpu"):
                    kpts_conf = kpts_conf.cpu().numpy()

                if isinstance(kpts_xy, np.ndarray) and len(kpts_xy) > 0:
                    for p_idx, person_kpts in enumerate(kpts_xy):
                        for wrist_idx in (9, 10):
                            if len(person_kpts) > wrist_idx:
                                wx, wy = person_kpts[wrist_idx]
                                conf = 1.0
                                if kpts_conf is not None and len(kpts_conf) > p_idx and len(kpts_conf[p_idx]) > wrist_idx:
                                    conf = float(kpts_conf[p_idx][wrist_idx])
                                if conf >= 0.48 and wx > 5 and wy > 5:
                                    dist = cv2.pointPolygonTest(self.polygon, (float(wx), float(wy)), False)
                                    if dist >= 0:
                                        wrist_in_basket = True
                                        if detections is not None and detections.tracker_id is not None and len(detections.tracker_id) > p_idx:
                                            wrist_person_id = int(detections.tracker_id[p_idx])
                                        wrist_conf = max(wrist_conf, conf)
                                        break
                        if wrist_in_basket:
                            break
            except Exception as e:
                logger.debug("Error testing wrist keypoints in basket: %s", e)

        # Proximity check for fallback
        person_near = False
        primary_person_id = None
        primary_conf = 0.85

        if len(detections) > 0 and detections.xyxy is not None:
            for i, box in enumerate(detections.xyxy):
                px1, py1, px2, py2 = box
                overlap_x = max(0.0, min(float(px2), float(bx + bw)) - max(float(px1), float(bx)))
                if overlap_x > 35 and py2 >= (by - 20) and py1 <= (by + bh):
                    person_near = True
                    if detections.tracker_id is not None and len(detections.tracker_id) > i:
                        primary_person_id = int(detections.tracker_id[i])
                    if detections.confidence is not None and len(detections.confidence) > i:
                        primary_conf = float(detections.confidence[i])
                    break

        # 4. Dynamic background difference strictly on the cropped basket ROI
        if self.ref_background is None or self.ref_background.shape != crop_frame.shape:
            self.ref_background = crop_frame.copy().astype(np.float32)

        ref_uint8 = cv2.convertScaleAbs(self.ref_background)
        diff = cv2.absdiff(crop_frame, ref_uint8)
        gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

        # Strictly masked to local basket polygon
        gray_diff_in_poly = cv2.bitwise_and(gray_diff, gray_diff, mask=crop_mask)

        # Foreground change threshold (intensity difference >= 32)
        _, change_mask = cv2.threshold(gray_diff_in_poly, 32, 255, cv2.THRESH_BINARY)
        changed_pixels = cv2.countNonZero(change_mask)

        # 5. Skin-tone filter on cropped ROI
        ycrcb = cv2.cvtColor(crop_frame, cv2.COLOR_BGR2YCrCb)
        skin_mask = cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127))
        skin_in_basket = cv2.bitwise_and(change_mask, skin_mask)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        skin_clean = cv2.morphologyEx(skin_in_basket, cv2.MORPH_OPEN, kernel)
        clean_skin_pixels = cv2.countNonZero(skin_clean)

        contours, _ = cv2.findContours(skin_clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        max_contour_area = max([cv2.contourArea(c) for c in contours], default=0.0)

        change_ratio = changed_pixels / float(poly_area)
        skin_ratio = clean_skin_pixels / float(poly_area)

        # 6. Advanced MediaPipe 21-Keypoint Hand Analysis on the crop
        hand_res = self.hand_analyzer.analyze(crop_frame)
        mediapipe_hand_in_basket = False
        if hand_res.detected:
            self.last_landmarks = hand_res.landmarks
            self.last_gesture = hand_res.gesture
            self.is_pinching = hand_res.is_pinching

            # Check if any finger tips or wrist lie within or close to basket polygon
            for lm in hand_res.landmarks:
                ax = x1 + lm[0] * crop_w
                ay = y1 + lm[1] * crop_h
                if cv2.pointPolygonTest(self.polygon, (float(ax), float(ay)), False) >= 0:
                    mediapipe_hand_in_basket = True
                    break

            if hand_res.is_pinching:
                self.interaction_pinch_occurred = True
            if hand_res.is_fist:
                self.interaction_fist_occurred = True
        else:
            self.last_landmarks = None
            self.last_gesture = "None"
            self.is_pinching = False

        # Hand detection criteria: MediaPipe hand / Pose wrist keypoint takes priority over color heuristics
        if mediapipe_hand_in_basket or wrist_in_basket:
            hand_detected = True
            if wrist_person_id is not None:
                primary_person_id = wrist_person_id
            if wrist_conf > 0:
                primary_conf = wrist_conf
        else:
            min_hand_blob = max(100.0, poly_area * 0.025)
            hand_detected = False
            if person_near:
                if max_contour_area >= min_hand_blob and change_ratio > 0.04 and skin_ratio > 0.02:
                    hand_detected = True

        # Slowly update reference background when no hand is in basket to adapt to ambient lighting
        if not hand_detected:
            cv2.accumulateWeighted(crop_frame, self.ref_background, 0.03, mask=crop_mask)

        self.hand_in_basket = hand_detected

        # 7. Vietnamese Banknote Detection with Majority Voting & Temporal Smoothing
        if hand_detected or hand_res.detected or self.state in (BasketState.HAND_ENTERING, BasketState.CASH_INTERACTING):
            curr_lbl, curr_c, curr_b = self.currency_detector.detect_currency(crop_frame)
            if curr_lbl and curr_b:
                self.currency_history.append((curr_lbl, curr_c, curr_b))
                if len(self.currency_history) > 30:
                    self.currency_history.pop(0)

                # Majority voting: determine the dominant denomination
                lbl_counts = Counter(x[0] for x in self.currency_history)
                top_lbl, vote_cnt = lbl_counts.most_common(1)[0]
                confs = [x[1] for x in self.currency_history if x[0] == top_lbl]
                avg_conf = float(np.mean(confs))

                self.detected_currency = top_lbl
                self.detected_currency_conf = avg_conf
                self.detected_currency_box = curr_b
        elif self.state == BasketState.IDLE and not hand_detected:
            self.detected_currency = None
            self.detected_currency_conf = 0.0
            self.detected_currency_box = None
            self.currency_history.clear()

        now = time.time()
        event_to_emit = None

        if hand_detected:
            if self.state == BasketState.IDLE:
                self.state = BasketState.HAND_ENTERING
                self.interaction_start_time = now
                self.interaction_frames = 1
                self.active_person_id = primary_person_id
                self.initial_pinch_or_fist = (self.is_pinching or getattr(self.hand_analyzer._last_result, "is_fist", False))
            elif self.state == BasketState.HAND_ENTERING:
                self.interaction_frames += 1
                if self.interaction_frames >= 3:
                    self.state = BasketState.CASH_INTERACTING
            elif self.state == BasketState.CASH_INTERACTING:
                self.interaction_frames += 1
                if self.is_pinching:
                    self.interaction_pinch_occurred = True
                if getattr(self.hand_analyzer._last_result, "is_fist", False):
                    self.interaction_fist_occurred = True
                self.final_pinch_or_fist = (self.is_pinching or getattr(self.hand_analyzer._last_result, "is_fist", False))
        else:
            if self.state in (BasketState.HAND_ENTERING, BasketState.CASH_INTERACTING):
                duration = now - self.interaction_start_time
                if self.state == BasketState.CASH_INTERACTING and duration >= 0.6:
                    # Smart Action Sequence Classification
                    if self.interaction_pinch_occurred or (self.final_pinch_or_fist and not self.initial_pinch_or_fist):
                        event_type = "RÚT TIỀN"
                        summary = f"Người #{self.active_person_id or 1} rút tiền từ rổ"
                    elif self.initial_pinch_or_fist and not self.final_pinch_or_fist:
                        event_type = "BỎ TIỀN"
                        summary = f"Người #{self.active_person_id or 1} bỏ tiền vào rổ"
                    elif not self.interaction_pinch_occurred and not self.interaction_fist_occurred and duration < 1.2:
                        event_type = "QUÉT QUA RỔ"
                        summary = f"Người #{self.active_person_id or 1} quét tay qua rổ tiền"
                    elif duration > 3.0:
                        event_type = "RÚT TIỀN"
                        summary = f"Người #{self.active_person_id or 1} rút tiền từ rổ"
                    elif duration > 1.2:
                        event_type = "BỎ TIỀN"
                        summary = f"Người #{self.active_person_id or 1} bỏ tiền vào rổ"
                    else:
                        event_type = "CHẠM RỔ"
                        summary = f"Người #{self.active_person_id or 1} chạm vào rổ tiền"

                    # Add gesture and currency details to summary
                    details = []
                    if self.last_gesture and self.last_gesture != "None":
                        details.append(f"Cử chỉ: {self.last_gesture}")
                    if self.detected_currency:
                        details.append(f"Mệnh giá: {self.detected_currency}")
                    details.append(f"{duration:.1f}s")
                    summary += f" ({' | '.join(details)})"

                    if (now - self.last_snapshot_time) >= self.snapshot_cooldown:
                        event_to_emit = {
                            "event_type": event_type,
                            "person_id": self.active_person_id,
                            "confidence": primary_conf,
                            "duration_seconds": duration,
                            "summary": summary,
                            "gesture": self.last_gesture,
                            "is_pinching": self.interaction_pinch_occurred,
                            "detected_currency": self.detected_currency,
                        }
                        self.last_snapshot_time = now

                self.state = BasketState.TRANSACTION_COMPLETE
            elif self.state == BasketState.TRANSACTION_COMPLETE:
                self.state = BasketState.IDLE
                self.active_person_id = None
                self.interaction_frames = 0
                self.interaction_pinch_occurred = False
                self.interaction_fist_occurred = False
                self.initial_pinch_or_fist = False
                self.final_pinch_or_fist = False
                self.detected_currency = None
                self.detected_currency_conf = 0.0
                self.detected_currency_box = None
                self.currency_history.clear()

        return self.state, event_to_emit

    def draw_basket(self, frame: np.ndarray) -> np.ndarray:
        """Annotates the Cash Basket ROI on the frame with high-contrast cyberpunk styling."""
        h_frame, w_frame = frame.shape[:2]
        self.ensure_frame_size(h_frame, w_frame)

        pts = self.polygon.reshape((-1, 1, 2))
        is_alert = self.hand_in_basket or self.state in (BasketState.HAND_ENTERING, BasketState.CASH_INTERACTING)

        # Base colors: BGR format
        if is_alert:
            border_color = (68, 68, 239)  # Bright Red / Crimson
            fill_color = (40, 40, 180)
            status_text = "CANH BAO: TAY DANG TRONG RO TIEN!"
            if self.state == BasketState.CASH_INTERACTING and self.interaction_start_time > 0:
                elapsed = time.time() - self.interaction_start_time
                status_text += f" ({elapsed:.1f}s)"
        else:
            border_color = (255, 230, 0)  # Cyan Neon
            fill_color = (180, 160, 0)
            status_text = "RO TIEN (CASH BASKET ROI)"

        # Local blending optimization: only blend the bounding box region
        bx, by, bw, bh = cv2.boundingRect(self.polygon)
        x1 = max(0, bx - 10)
        y1 = max(0, by - 10)
        x2 = min(w_frame, bx + bw + 10)
        y2 = min(h_frame, by + bh + 10)

        if (x2 - x1) > 0 and (y2 - y1) > 0:
            sub = frame[y1:y2, x1:x2]
            sub_overlay = sub.copy()
            local_pts = (self.polygon - np.array([x1, y1])).reshape((-1, 1, 2))
            cv2.fillPoly(sub_overlay, [local_pts], fill_color)
            cv2.addWeighted(sub_overlay, 0.20 if is_alert else 0.08, sub, 0.80 if is_alert else 0.92, 0, sub)

        # Draw polygon border
        cv2.polylines(frame, [pts], isClosed=True, color=border_color, thickness=2 if not is_alert else 3)

        # Draw label at top left of basket
        label_pos = (max(10, bx), max(25, by - 8))
        label_size, _ = cv2.getTextSize(status_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(
            frame,
            (label_pos[0] - 2, label_pos[1] - label_size[1] - 4),
            (label_pos[0] + label_size[0] + 4, label_pos[1] + 4),
            (15, 23, 42),
            -1,
        )
        cv2.putText(
            frame,
            status_text,
            label_pos,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            border_color,
            1,
            cv2.LINE_AA,
        )

        # Draw MediaPipe 21-keypoint cyber hand skeleton if detected
        if self.last_landmarks and self.last_crop_bbox:
            cx1, cy1, cw, ch = self.last_crop_bbox
            frame = draw_cyber_hand_skeleton(
                scene=frame,
                landmarks=self.last_landmarks,
                crop_x1=cx1,
                crop_y1=cy1,
                crop_w=cw,
                crop_h=ch,
                is_pinching=self.is_pinching,
                gesture_name=self.last_gesture,
                currency_label=self.detected_currency,
            )

        # Draw detected currency bounding box and neon badge if detected
        if self.detected_currency and self.detected_currency_box and self.last_crop_bbox:
            cx1, cy1, cw, ch = self.last_crop_bbox
            frame = draw_detected_currency(
                scene=frame,
                crop_x1=cx1,
                crop_y1=cy1,
                label=self.detected_currency,
                confidence=self.detected_currency_conf,
                box_crop=self.detected_currency_box,
            )

        return frame



# COCO Skeleton Bone Connections with Biomechanical Max-Length Constraints
# (u, v, max_length_px) scaled to 640x480 reference space
SKELETON_SPECS = [
    # Arms: shoulders -> elbows -> wrists
    (5, 7, 130), (7, 9, 120),
    (6, 8, 130), (8, 10, 120),
    # Torso & Shoulders
    (5, 6, 140), (5, 11, 160), (6, 12, 160), (11, 12, 120),
    # Legs: hips -> knees -> ankles
    (11, 13, 160), (13, 15, 160),
    (12, 14, 160), (14, 16, 160),
    # Head & Neck
    (0, 1, 50), (0, 2, 50), (1, 3, 50), (2, 4, 50), (0, 5, 80), (0, 6, 80),
]


def draw_skeleton_overlay(
    frame: np.ndarray,
    keypoints_xy: np.ndarray,
    keypoints_conf: np.ndarray | None,
    basket_polygon: np.ndarray | None = None,
) -> np.ndarray:
    """Draws sleek cyber-neon skeleton bones with strict confidence & max-length biomechanical filtering."""
    if keypoints_xy is None or len(keypoints_xy) == 0:
        return frame

    h_frame, w_frame = frame.shape[:2]
    # Length scale factor for native resolution (vs 640x480)
    len_scale = max(w_frame / 640.0, h_frame / 480.0)

    for person_idx, kpts in enumerate(keypoints_xy):
        confs = keypoints_conf[person_idx] if keypoints_conf is not None and len(keypoints_conf) > person_idx else None
        
        # 1. Bones: Draw only when both joints have high confidence (>= 0.45) AND plausible distance
        for (u, v, max_len_ref) in SKELETON_SPECS:
            if u < len(kpts) and v < len(kpts):
                cu = confs[u] if confs is not None and len(confs) > u else 1.0
                cv = confs[v] if confs is not None and len(confs) > v else 1.0
                if cu >= 0.45 and cv >= 0.45:
                    x1, y1 = float(kpts[u][0]), float(kpts[u][1])
                    x2, y2 = float(kpts[v][0]), float(kpts[v][1])
                    if x1 > 5 and y1 > 5 and x2 > 5 and y2 > 5:
                        # Biological sanity check: reject lines stretching across the entire kitchen
                        dist = np.hypot(x1 - x2, y1 - y2)
                        if dist <= (max_len_ref * len_scale):
                            pt1 = (int(round(x1)), int(round(y1)))
                            pt2 = (int(round(x2)), int(round(y2)))
                            # Cyan neon bone: BGR (255, 230, 0)
                            cv2.line(frame, pt1, pt2, (255, 230, 0), 2, cv2.LINE_AA)

        # 2. Joints & Wrists
        for j_idx, pt in enumerate(kpts):
            cj = confs[j_idx] if confs is not None and len(confs) > j_idx else 1.0
            if cj >= 0.48:
                px, py = int(round(pt[0])), int(round(pt[1]))
                if 5 < px < (w_frame - 5) and 5 < py < (h_frame - 5):
                    if j_idx in (9, 10):
                        # Wrist: left (9) / right (10)
                        in_basket = False
                        if basket_polygon is not None and len(basket_polygon) >= 3:
                            in_basket = cv2.pointPolygonTest(basket_polygon, (float(px), float(py)), False) >= 0

                        # Crimson if in basket, Amber Gold if free
                        color = (68, 68, 239) if in_basket else (0, 214, 255)
                        cv2.circle(frame, (px, py), 5, color, -1, cv2.LINE_AA)
                        cv2.circle(frame, (px, py), 8, (255, 255, 255), 1, cv2.LINE_AA)
                    elif j_idx in (5, 6, 11, 12):
                        # Main body anchor joints: small emerald dot
                        cv2.circle(frame, (px, py), 3, (118, 230, 0), -1, cv2.LINE_AA)
    return frame


class LiveWebcamService:
    def __init__(self, model_path: Path | str | None = None, source: Union[int, str] = 0):
        self.source = source
        self.model_path = _resolve_model_path(model_path)
        self._cap: cv2.VideoCapture | None = None
        self._model = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.RLock()
        self._latest_jpeg: bytes | None = None
        self._latest_raw_frame: np.ndarray | None = None
        self._frame_seq = 0
        self._frame_condition = threading.Condition(self._lock)

        # Evidence manager & Cash basket tracker
        self._evidence_manager = EvidenceManager()
        self._basket_tracker = CashBasketTracker()

        # Supervision Annotators & Config
        self.enable_traces = False
        self.box_style = "corner"  # 'corner' or 'box'
        self.enable_zone = True
        self.enable_basket = True
        self.flip_h = True  # Flip horizontally (mirror - default for webcam)
        self.flip_v = False  # Flip vertically (ceiling mounted)
        self.enable_video_simulation = True  # Feature flag for video simulation mode
        self.loop_video = True  # Loop simulated video file seamlessly
        self._people_in_zone = 0

        # AI Model, Precision & Pose Config
        self.model_mode = "yolo_pose"  # 'yolo_pose' or 'yolo_detect'
        self.precision_mode = "cuda_fp16"  # 'cuda_fp16', 'onnx', 'cpu'
        self.enable_skeleton = True

        self._corner_annotator = sv.BoxCornerAnnotator(
            color=CCTV_PALETTE,
            thickness=2,
            corner_length=15,
            color_lookup=sv.ColorLookup.TRACK,
        )
        self._box_annotator = sv.BoxAnnotator(
            color=CCTV_PALETTE,
            thickness=2,
            color_lookup=sv.ColorLookup.TRACK,
        )
        self._label_annotator = sv.LabelAnnotator(
            color=CCTV_PALETTE,
            text_color=sv.Color.from_hex("#0F172A"),
            text_scale=0.5,
            text_thickness=1,
            text_padding=4,
            border_radius=4,
            color_lookup=sv.ColorLookup.TRACK,
        )
        self._trace_annotator = sv.TraceAnnotator(
            color=CCTV_PALETTE,
            thickness=2,
            trace_length=35,
            color_lookup=sv.ColorLookup.TRACK,
        )

        # Center security zone / cashier monitoring polygon (640x480 reference space)
        self._base_zone_polygon = [
            [160, 160],
            [480, 160],
            [520, 440],
            [120, 440],
        ]
        self._zone_polygon = np.array(self._base_zone_polygon, dtype=np.int32)
        self._zone_frame_size = (480, 640)
        self._zone = sv.PolygonZone(polygon=self._zone_polygon)
        self._zone_annotator = sv.PolygonZoneAnnotator(
            zone=self._zone,
            color=sv.Color.from_hex("#38BDF8"),  # Titanium Cyan (Apple Pro)
            thickness=2,
            text_scale=0.5,
            text_thickness=1,
            text_padding=4,
            display_in_zone_count=False,
        )

        # Stats
        self._device_name = "CPU"
        self._inference_device = "cpu"
        self._fps = 0.0
        self._inference_ms = 0.0
        self._people_count = 0
        self._last_error: str | None = None

        self._people_in_zone_ids = []
        self._init_device()
        self._load_config()

    def _ensure_zone_for_frame(self, h_frame: int, w_frame: int) -> None:
        """Dynamically scales base security zone (640x480 reference space) to native frame dimensions."""
        if (h_frame, w_frame) == getattr(self, "_zone_frame_size", (0, 0)):
            return

        scale_x = w_frame / 640.0
        scale_y = h_frame / 480.0
        scaled = []
        for p in self._base_zone_polygon:
            px = p[0] * w_frame if p[0] <= 1.0 else p[0] * scale_x
            py = p[1] * h_frame if p[1] <= 1.0 else p[1] * scale_y
            scaled.append([int(round(px)), int(round(py))])

        self._zone_polygon = np.array(scaled, dtype=np.int32)
        self._zone = sv.PolygonZone(polygon=self._zone_polygon)
        self._zone_annotator = sv.PolygonZoneAnnotator(
            zone=self._zone,
            color=sv.Color.from_hex("#38BDF8"),
            thickness=2,
            text_scale=0.5,
            text_thickness=1,
            text_padding=4,
            display_in_zone_count=False,
        )
        self._zone_frame_size = (h_frame, w_frame)

    def _load_config(self) -> None:
        cfg_path = _resolve_config_path()
        if not cfg_path.is_file():
            return
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if "basket_roi" in data and len(data["basket_roi"]) >= 3:
                self._basket_tracker.set_roi(data["basket_roi"])
            if "security_zone_roi" in data and len(data["security_zone_roi"]) >= 3:
                self._base_zone_polygon = [list(p) for p in data["security_zone_roi"]]
                self._zone_frame_size = (0, 0)
                self._zone_polygon = np.array(self._base_zone_polygon, dtype=np.int32)
                self._zone = sv.PolygonZone(polygon=self._zone_polygon)
                self._zone_annotator = sv.PolygonZoneAnnotator(
                    zone=self._zone,
                    color=sv.Color.from_hex("#38BDF8"),
                    thickness=2,
                    text_scale=0.5,
                    text_thickness=1,
                    text_padding=4,
                    display_in_zone_count=False,
                )
            if "flip_h" in data:
                self.flip_h = bool(data["flip_h"])
            if "flip_v" in data:
                self.flip_v = bool(data["flip_v"])
            if "enable_basket" in data:
                self.enable_basket = bool(data["enable_basket"])
            if "enable_zone" in data:
                self.enable_zone = bool(data["enable_zone"])
            if "enable_video_simulation" in data:
                self.enable_video_simulation = bool(data["enable_video_simulation"])
            if "loop_video" in data:
                self.loop_video = bool(data["loop_video"])
            if "model_mode" in data and data["model_mode"] in ("yolo_pose", "yolo_detect"):
                self.model_mode = str(data["model_mode"])
            if "precision_mode" in data and data["precision_mode"] in ("cuda_fp16", "onnx", "cpu"):
                self.precision_mode = str(data["precision_mode"])
            if "enable_skeleton" in data:
                self.enable_skeleton = bool(data["enable_skeleton"])

            telegram_notifier.update_config(
                token=data.get("telegram_token"),
                chat_id=data.get("telegram_chat_id"),
                enabled=data.get("telegram_enabled", True) if data.get("telegram_enabled") is not None else True,
                events=data.get("telegram_events"),
            )
            logger.info("Loaded persistent live webcam config from %s", cfg_path)
        except Exception as exc:
            logger.warning("Failed to load live config: %s", exc)

    def _save_config(self) -> None:
        try:
            cfg_path = _resolve_config_path()
            tg = telegram_notifier.get_status()
            data = {
                "basket_roi": self._basket_tracker.base_points,
                "security_zone_roi": self._base_zone_polygon,
                "flip_h": self.flip_h,
                "flip_v": self.flip_v,
                "enable_basket": self.enable_basket,
                "enable_zone": self.enable_zone,
                "enable_video_simulation": self.enable_video_simulation,
                "loop_video": self.loop_video,
                "model_mode": self.model_mode,
                "precision_mode": self.precision_mode,
                "enable_skeleton": self.enable_skeleton,
                "telegram_token": telegram_notifier.token if tg.get("has_token") else "",
                "telegram_chat_id": tg.get("chat_id", ""),
                "telegram_enabled": tg.get("enabled", False),
                "telegram_events": tg.get("events", ["RÚT TIỀN", "BỎ TIỀN", "CHẠM RỔ"]),
            }
            tmp_path = cfg_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(cfg_path)
            logger.info("Persisted live webcam config to %s", cfg_path)
        except Exception as exc:
            logger.warning("Failed to persist live config: %s", exc)

    def _init_device(self) -> None:
        try:
            import torch
            if torch.cuda.is_available():
                self._inference_device = "0"
                self._device_name = torch.cuda.get_device_name(0)
            else:
                self._inference_device = "cpu"
                self._device_name = "CPU"
        except Exception:
            self._inference_device = "cpu"
            self._device_name = "CPU"

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        from ultralytics import YOLO

        if self.model_mode == "yolo_pose":
            if self.precision_mode == "onnx":
                pose_path = _resolve_onnx_pose_model_path()
                logger.info("Loading YOLO-Pose ONNX model from %s", pose_path)
                self._model = YOLO(str(pose_path), task="pose")
            else:
                pose_path = _resolve_pose_model_path()
                logger.info("Loading YOLO-Pose model from %s on device %s (precision=%s)", pose_path, self._inference_device, self.precision_mode)
                self._model = YOLO(str(pose_path))
        else:
            target = _resolve_model_path(self.model_path)
            logger.info("Loading YOLO model from %s on device %s", target, self._inference_device)
            self._model = YOLO(str(target))
        return self._model

    def _open_camera(self, source: Union[int, str]) -> cv2.VideoCapture | None:
        """Open camera device (webcam or RTSP/file stream) with optimized DirectShow or RTSP TCP transport."""
        if isinstance(source, int) or (isinstance(source, str) and source.strip().isdigit()):
            idx = int(source)
            if os.name == "nt":
                try:
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        return cap
                except Exception as exc:
                    logger.warning("DirectShow camera open failed: %s", exc)
            cap = cv2.VideoCapture(idx)
            if cap.isOpened():
                return cap
            return None

        # String source (RTSP stream or video file)
        source_str = str(source).strip()
        logger.info("Opening external camera source: %s", source_str)
        if source_str.startswith("rtsp://") or source_str.startswith("rtsps://"):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|max_delay;500000"

        cap = cv2.VideoCapture(source_str)
        if cap.isOpened():
            return cap
        return None

    def start(self, source: Union[int, str, None] = None, loop: bool | None = None) -> dict:
        with self._lock:
            if source is not None:
                self.source = source
            if loop is not None:
                self.loop_video = bool(loop)

            if self._running:
                return self.status()

            self._last_error = None
            self._basket_tracker.reset()
            self._people_count = 0
            self._people_in_zone = 0
            self._people_in_zone_ids = []
            cap = self._open_camera(self.source)
            if not cap:
                self._last_error = f"Không thể mở nguồn camera [{self.source}]"
                return self.status()

            # Set resolution for webcams (ignored harmlessly for RTSP/files)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            self._cap = cap
            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, name="live-webcam-loop", daemon=True)
            self._thread.start()

        return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        with self._lock:
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            self._latest_jpeg = None
            self._latest_raw_frame = None
            self._people_count = 0
            self._people_in_zone = 0
            self._people_in_zone_ids = []
            self._fps = 0.0
            self._basket_tracker.reset()

        with self._frame_condition:
            self._frame_condition.notify_all()

        return self.status()

    def update_config(
        self,
        enable_traces: bool | None = None,
        box_style: str | None = None,
        enable_zone: bool | None = None,
        enable_basket: bool | None = None,
        flip_h: bool | None = None,
        flip_v: bool | None = None,
        enable_video_simulation: bool | None = None,
        loop_video: bool | None = None,
        model_mode: str | None = None,
        precision_mode: str | None = None,
        enable_skeleton: bool | None = None,
        telegram_token: str | None = None,
        telegram_chat_id: str | None = None,
        telegram_enabled: bool | None = None,
        telegram_events: list[str] | None = None,
    ) -> dict:
        with self._lock:
            if enable_traces is not None:
                self.enable_traces = bool(enable_traces)
            if box_style in ("corner", "box"):
                self.box_style = box_style
            if enable_zone is not None:
                self.enable_zone = bool(enable_zone)
            if enable_basket is not None:
                self.enable_basket = bool(enable_basket)
            if flip_h is not None:
                self.flip_h = bool(flip_h)
            if flip_v is not None:
                self.flip_v = bool(flip_v)
            if enable_video_simulation is not None:
                self.enable_video_simulation = bool(enable_video_simulation)
            if loop_video is not None:
                self.loop_video = bool(loop_video)
            if model_mode in ("yolo_pose", "yolo_detect") and model_mode != self.model_mode:
                self.model_mode = model_mode
                self._model = None
            if precision_mode in ("cuda_fp16", "onnx", "cpu") and precision_mode != self.precision_mode:
                self.precision_mode = precision_mode
                self._model = None
            if enable_skeleton is not None:
                self.enable_skeleton = bool(enable_skeleton)

            if telegram_token is not None or telegram_chat_id is not None or telegram_enabled is not None or telegram_events is not None:
                telegram_notifier.update_config(
                    token=telegram_token,
                    chat_id=telegram_chat_id,
                    enabled=telegram_enabled,
                    events=telegram_events,
                )
            self._save_config()
        return self.status()

    def set_basket_roi(self, points: list[list[int]]) -> dict:
        with self._lock:
            self._basket_tracker.set_roi(points)
            self._save_config()
        return self.status()

    def set_zone_roi(self, points: list[list[int]]) -> dict:
        with self._lock:
            self._base_zone_polygon = [list(p) for p in points]
            self._zone_frame_size = (0, 0)
            self._zone_polygon = np.array(points, dtype=np.int32)
            self._zone = sv.PolygonZone(polygon=self._zone_polygon)
            self._zone_annotator = sv.PolygonZoneAnnotator(
                zone=self._zone,
                color=sv.Color.from_hex("#38BDF8"),
                thickness=2,
                text_scale=0.5,
                text_thickness=1,
                text_padding=4,
                display_in_zone_count=False,
            )
            self._save_config()
        return self.status()

    def capture_snapshot(self, event_type: str = "CHỤP THỦ CÔNG", summary: str = "") -> dict | None:
        with self._lock:
            if self._latest_raw_frame is None:
                return None
            frame_to_save = self._latest_raw_frame.copy()
            active_pid = self._basket_tracker.active_person_id

        return self._evidence_manager.save_evidence(
            frame=frame_to_save,
            event_type=event_type,
            person_id=active_pid,
            confidence=0.99,
            duration_seconds=0.0,
            summary=summary or "Người dùng chụp ảnh bằng chứng thủ công",
        )

    def list_evidence(self) -> list[dict]:
        return self._evidence_manager.list_evidence()

    def get_evidence_path(self, filename: str) -> Path | None:
        return self._evidence_manager.get_image_path(filename)

    def clear_evidence(self) -> int:
        return self._evidence_manager.clear_all()

    def status(self) -> dict:
        with self._lock:
            running = self._running
            return {
                "running": running,
                "source": str(self.source),
                "device_index": self.source if isinstance(self.source, int) else 0,
                "device_name": self._device_name,
                "people_count": self._people_count if running else 0,
                "people_in_zone": self._people_in_zone if running else 0,
                "people_in_zone_ids": getattr(self, "_people_in_zone_ids", []) if running else [],
                "basket_roi": self._basket_tracker.base_points,
                "security_zone_roi": self._base_zone_polygon,
                "fps": round(self._fps, 1) if running else 0.0,
                "inference_ms": round(self._inference_ms, 1) if running else 0.0,
                "frame_available": (self._latest_jpeg is not None) if running else False,
                "enable_traces": self.enable_traces,
                "box_style": self.box_style,
                "enable_zone": self.enable_zone,
                "enable_basket": self.enable_basket,
                "flip_h": self.flip_h,
                "flip_v": self.flip_v,
                "enable_video_simulation": getattr(self, "enable_video_simulation", True),
                "loop_video": getattr(self, "loop_video", True),
                "is_simulated_video": isinstance(self.source, str) and not self.source.startswith(("rtsp://", "rtsps://")),
                "basket_state": self._basket_tracker.state if running else BasketState.IDLE,
                "hand_in_basket": self._basket_tracker.hand_in_basket if running else False,
                "active_person_id": self._basket_tracker.active_person_id if running else None,
                "evidence_count": self._evidence_manager.count(),
                "model_mode": self.model_mode,
                "precision_mode": self.precision_mode,
                "enable_skeleton": self.enable_skeleton,
                "hand_gesture": getattr(self._basket_tracker, "last_gesture", "None") if running else "None",
                "is_pinching": getattr(self._basket_tracker, "is_pinching", False) if running else False,
                "detected_currency": getattr(self._basket_tracker, "detected_currency", None) if running else None,
                "hand_landmarks_count": 21 if (running and getattr(self._basket_tracker, "last_landmarks", None) is not None) else 0,
                "advanced_ai_models": [
                    "MediaPipe Hand Landmarker 21-Points",
                    "Vietnamese Currency YOLO",
                    "ATM Theft Detector",
                    "YOLO11 Pose Estimation",
                ],
                "telegram": telegram_notifier.get_status(),
                "last_error": self._last_error,
            }

    def get_latest_frame(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def _capture_loop(self) -> None:
        try:
            model = self._ensure_model()
        except Exception as exc:
            logger.exception("Failed to initialize model for live webcam")
            with self._lock:
                self._last_error = f"Lỗi nạp model: {exc}"
                self._running = False
                if self._cap:
                    self._cap.release()
                    self._cap = None
            return

        frame_times = []
        is_file_stream = isinstance(self.source, str) and not self.source.startswith(("rtsp://", "rtsps://"))
        target_fps = 30.0
        if is_file_stream and self._cap:
            try:
                native_fps = self._cap.get(cv2.CAP_PROP_FPS)
                if native_fps and 5.0 <= native_fps <= 60.0:
                    target_fps = float(native_fps)
            except Exception:
                pass
        target_interval = 1.0 / target_fps
        frame_idx = 0
        last_t_infer = 0.0
        prev_detections = sv.Detections.empty()
        prev_keypoints = None

        while True:
            with self._lock:
                if not self._running or not self._cap:
                    break
                cap = self._cap

            loop_start = time.perf_counter()
            ret, frame = cap.read()
            if not ret or frame is None:
                if is_file_stream:
                    if getattr(self, "loop_video", True):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        self._basket_tracker.reset()
                        frame_idx = 0
                        time.sleep(0.02)
                        continue
                    else:
                        with self._lock:
                            self._running = False
                        break
                time.sleep(0.01)
                continue

            frame_idx += 1
            h_frame, w_frame = frame.shape[:2]
            self._ensure_zone_for_frame(h_frame, w_frame)
            self._basket_tracker.ensure_frame_size(h_frame, w_frame)

            # Apply camera orientation flip (mirror / ceiling mounted)
            if self.flip_h and self.flip_v:
                frame = cv2.flip(frame, -1)
            elif self.flip_h:
                frame = cv2.flip(frame, 1)
            elif self.flip_v:
                frame = cv2.flip(frame, 0)

            t0 = time.perf_counter()
            people_count = 0
            people_in_zone = 0
            event_triggered = None
            annotated_frame = frame.copy()

            try:
                # Optimized inference cadence:
                # Run YOLO detection with imgsz=640. For file streams, run every 2 frames to achieve 25-30+ FPS effortlessly.
                should_run_yolo = True
                if is_file_stream and (frame_idx % 2 != 0) and len(prev_detections) > 0:
                    should_run_yolo = False

                kpts_obj = None
                if should_run_yolo:
                    use_half = (self.precision_mode == "cuda_fp16" and self._inference_device != "cpu")
                    classes_arg = None if self.model_mode == "yolo_pose" else [0]
                    results = model.track(
                        frame,
                        classes=classes_arg,
                        device=self._inference_device,
                        imgsz=640,
                        persist=True,
                        verbose=False,
                        half=use_half,
                    )
                    t_infer = (time.perf_counter() - t0) * 1000.0
                    last_t_infer = t_infer

                    detections = sv.Detections.empty()
                    if results and len(results) > 0:
                        res = results[0]
                        kpts_obj = getattr(res, "keypoints", None)
                        det = sv.Detections.from_ultralytics(res)
                        if det.class_id is not None and len(det) > 0:
                            det = det[det.class_id == 0]
                        detections = det
                    prev_detections = detections
                    prev_keypoints = kpts_obj
                else:
                    detections = prev_detections
                    kpts_obj = prev_keypoints
                    t_infer = last_t_infer

                people_count = len(detections)

                if people_count > 0:
                    if detections.tracker_id is None:
                        detections.tracker_id = np.arange(len(detections))

                    # 1. Supervision Traces (movement trail)
                    if self.enable_traces:
                        annotated_frame = self._trace_annotator.annotate(scene=annotated_frame, detections=detections)

                    # 2. Supervision Security Zone & Khách Tại Quầy detection
                    people_in_zone_ids = []
                    if self.enable_zone:
                        zone_triggers = self._zone.trigger(detections=detections)
                        annotated_frame = self._zone_annotator.annotate(scene=annotated_frame)

                        zx, zy, zw, zh = cv2.boundingRect(self._zone_polygon)
                        for i, box in enumerate(detections.xyxy):
                            px1, py1, px2, py2 = box
                            tid = int(detections.tracker_id[i]) if detections.tracker_id is not None and len(detections.tracker_id) > i else (i + 1)
                            inter_x = max(0.0, min(float(px2), float(zx + zw)) - max(float(px1), float(zx)))
                            inter_y = max(0.0, min(float(py2), float(zy + zh)) - max(float(py1), float(zy)))
                            pcx, pcy = float(px1 + px2) / 2.0, float(py1 + py2) / 2.0
                            
                            is_triggered = bool(zone_triggers[i]) if (zone_triggers is not None and len(zone_triggers) > i) else False
                            in_poly_center = cv2.pointPolygonTest(self._zone_polygon, (pcx, pcy), False) >= 0
                            in_poly_bottom = cv2.pointPolygonTest(self._zone_polygon, (pcx, float(py2)), False) >= 0
                            has_overlap = (inter_x > 30 and inter_y > 40)

                            if is_triggered or in_poly_center or in_poly_bottom or has_overlap:
                                people_in_zone_ids.append(tid)

                    self._people_in_zone_ids = people_in_zone_ids
                    people_in_zone = len(people_in_zone_ids)

                    # 3. Supervision Boxes (Corner or Standard Box)
                    if self.box_style == "box":
                        annotated_frame = self._box_annotator.annotate(scene=annotated_frame, detections=detections)
                    else:
                        annotated_frame = self._corner_annotator.annotate(scene=annotated_frame, detections=detections)

                    # 4. Supervision Labels (colored badge matching tracker id)
                    labels = []
                    confs = detections.confidence if detections.confidence is not None else [0.0] * people_count
                    for tid, conf in zip(detections.tracker_id, confs):
                        if tid in people_in_zone_ids:
                            labels.append(f"Nguoi #{tid} [Khach tai quay]: {int(conf * 100)}%")
                        else:
                            labels.append(f"Nguoi #{tid}: {int(conf * 100)}%")
                    annotated_frame = self._label_annotator.annotate(scene=annotated_frame, detections=detections, labels=labels)
                elif self.enable_zone:
                    empty_dets = sv.Detections.empty()
                    self._zone.trigger(detections=empty_dets)
                    people_in_zone = 0
                    annotated_frame = self._zone_annotator.annotate(scene=annotated_frame)

                # 5. Cash Basket Interaction Tracking & Evidence Capture
                if self.enable_basket:
                    _, event_triggered = self._basket_tracker.process_frame(frame, detections, kpts_obj)
                    annotated_frame = self._basket_tracker.draw_basket(annotated_frame)

                # 6. Pose Skeleton & Wrist Keypoints Visualization
                if self.enable_skeleton and kpts_obj is not None:
                    k_xy = getattr(kpts_obj, "xy", None)
                    k_conf = getattr(kpts_obj, "conf", None)
                    if hasattr(k_xy, "cpu"):
                        k_xy = k_xy.cpu().numpy()
                    if hasattr(k_conf, "cpu") and k_conf is not None:
                        k_conf = k_conf.cpu().numpy()
                    if k_xy is not None and len(k_xy) > 0:
                        annotated_frame = draw_skeleton_overlay(
                            annotated_frame,
                            k_xy,
                            k_conf,
                            self._basket_tracker.polygon if self.enable_basket else None,
                        )

            except Exception as exc:
                t_infer = 0.0
                logger.warning("Error during live inference frame: %s", exc)

            # Automatically save evidence snapshot if an interaction event was triggered
            if event_triggered:
                try:
                    logger.info("Cash basket event triggered: %s", event_triggered)
                    self._evidence_manager.save_evidence(
                        frame=annotated_frame,
                        event_type=event_triggered["event_type"],
                        person_id=event_triggered.get("person_id"),
                        confidence=event_triggered.get("confidence", 0.85),
                        duration_seconds=event_triggered.get("duration_seconds", 0.0),
                        summary=event_triggered.get("summary", ""),
                    )
                except Exception as exc:
                    logger.warning("Failed to auto-save evidence snapshot: %s", exc)

                try:
                    ok_enc, enc_jpeg = cv2.imencode(".jpg", annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    if ok_enc:
                        telegram_notifier.queue_alert(enc_jpeg.tobytes(), event_triggered)
                except Exception as exc:
                    logger.warning("Failed to dispatch Telegram alert: %s", exc)

            # Compute FPS
            now = time.perf_counter()
            frame_times.append(now)
            if len(frame_times) > 30:
                frame_times.pop(0)
            fps = len(frame_times) / (frame_times[-1] - frame_times[0]) if len(frame_times) > 1 else 0.0

            # Draw HUD info banner overlay
            basket_indicator = ""
            if self.enable_basket:
                if self._basket_tracker.hand_in_basket:
                    basket_indicator = " | RO TIEN: DANG THO TAY!"
                else:
                    basket_indicator = " | RO TIEN: OK"

            hud_text = f"CCTV AI + CASH BASKET | {self._device_name} | {fps:.1f} FPS | {t_infer:.1f}ms | {people_count} person{basket_indicator}"
            if self.enable_zone:
                if len(self._people_in_zone_ids) > 0:
                    id_str = ", ".join([f"#{pid}" for pid in self._people_in_zone_ids])
                    hud_text += f" | KHACH TAI QUAY: {id_str} ({len(self._people_in_zone_ids)} nguoi)"
                elif people_in_zone > 0:
                    hud_text += f" | QUAY: {people_in_zone} nguoi"

            cv2.rectangle(annotated_frame, (10, 10), (14 + len(hud_text) * 9, 36), (15, 23, 42), -1)
            hud_color = (68, 68, 239) if self._basket_tracker.hand_in_basket else (248, 189, 56)
            cv2.putText(annotated_frame, hud_text, (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.42, hud_color, 1, cv2.LINE_AA)

            # Encode frame to JPEG
            ok, buf = cv2.imencode(".jpg", annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                jpeg_bytes = buf.tobytes()
                with self._frame_condition:
                    self._latest_jpeg = jpeg_bytes
                    self._latest_raw_frame = annotated_frame
                    self._frame_seq += 1
                    self._people_count = people_count
                    self._fps = fps
                    self._inference_ms = t_infer
                    self._frame_condition.notify_all()

            if is_file_stream:
                elapsed = time.perf_counter() - loop_start
                delay = target_interval - elapsed
                if delay > 0.002:
                    time.sleep(delay)
                elif delay < -0.040:
                    # Video playback fell behind real-time schedule:
                    # Skip decode on next frame to catch up and prevent slow-motion lag
                    cap.grab()
            else:
                time.sleep(0.003)

        # Cleanup at loop exit
        with self._lock:
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

    def generate_frames(self) -> Generator[bytes, None, None]:
        last_seq = -1
        try:
            while True:
                with self._frame_condition:
                    while self._running and (self._latest_jpeg is None or self._frame_seq == last_seq):
                        if not self._frame_condition.wait(timeout=0.5):
                            if not self._running:
                                break

                    if not self._running:
                        break

                    frame_bytes = self._latest_jpeg
                    last_seq = self._frame_seq

                if frame_bytes:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(frame_bytes)}\r\n\r\n".encode("ascii")
                        + frame_bytes
                        + b"\r\n"
                    )
        except GeneratorExit:
            logger.debug("Live stream client disconnected")


# Global singleton instance for the app lifecycle
webcam_service = LiveWebcamService()
