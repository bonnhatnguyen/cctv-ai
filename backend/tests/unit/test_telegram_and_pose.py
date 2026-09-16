"""Unit tests for Telegram Notifier, YOLO11-Pose wrist tracking, and AI model acceleration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import supervision as sv

from app.live.api import router as live_webcam_router
from app.live.telegram import TelegramNotifier
from app.live.webcam import BasketState, CashBasketTracker, LiveWebcamService, draw_skeleton_overlay


def test_telegram_notifier_lifecycle():
    notifier = TelegramNotifier(token="test_token", chat_id="12345", enabled=True)
    status = notifier.get_status()
    assert status["configured"] is True
    assert status["enabled"] is True
    assert status["chat_id"] == "12345"
    assert "RÚT TIỀN" in status["events"]

    notifier.update_config(enabled=False, chat_id="67890")
    status = notifier.get_status()
    assert status["enabled"] is False
    assert status["chat_id"] == "67890"


def test_telegram_notifier_detect_chat_id():
    notifier = TelegramNotifier(token="mock_token")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 100,
                "message": {
                    "chat": {"id": 99887766, "first_name": "Bao"},
                    "text": "/start",
                },
            }
        ],
    }

    with patch("requests.get", return_value=mock_resp):
        res = notifier.detect_chat_id()
        assert res["success"] is True
        assert res["chat_id"] == "99887766"
        assert res["sender_name"] == "Bao"
        assert notifier.chat_id == "99887766"
        assert notifier.enabled is True


def test_telegram_notifier_queue_alert():
    notifier = TelegramNotifier(token="mock_token", chat_id="12345", enabled=True, cooldown_seconds=0.0)
    with patch.object(notifier, "_dispatch_alert") as mock_dispatch:
        event = {
            "event_type": "RÚT TIỀN",
            "duration_seconds": 3.5,
            "person_id": 1,
            "confidence": 0.95,
            "summary": "Rút tiền test",
        }
        notifier.queue_alert(b"fake_jpeg", event)
        # Give worker thread a moment to pick from queue
        import time
        time.sleep(0.2)
        assert mock_dispatch.called


def test_cash_basket_tracker_wrist_keypoint_interaction():
    tracker = CashBasketTracker(polygon=[[100, 100], [400, 100], [400, 400], [100, 400]])
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    empty_dets = sv.Detections.empty()

    # 1. No keypoints -> idle
    state, event = tracker.process_frame(dummy_frame, empty_dets, keypoints=None)
    assert state == BasketState.IDLE
    assert tracker.hand_in_basket is False

    # 2. Keypoints with wrist OUTSIDE basket (e.g. at (50, 50))
    # 17 keypoints, wrist is 9 and 10
    kpts_outside = np.zeros((1, 17, 2), dtype=np.float32)
    kpts_outside[0, 9] = [50.0, 50.0]
    kpts_outside[0, 10] = [60.0, 60.0]
    kpts_conf = np.ones((1, 17), dtype=np.float32)

    mock_kpts_outside = MagicMock()
    mock_kpts_outside.xy = kpts_outside
    mock_kpts_outside.conf = kpts_conf

    state, event = tracker.process_frame(dummy_frame, empty_dets, keypoints=mock_kpts_outside)
    assert tracker.hand_in_basket is False

    # 3. Keypoints with right wrist INSIDE basket (e.g. at (320, 350))
    kpts_inside = np.zeros((1, 17, 2), dtype=np.float32)
    kpts_inside[0, 10] = [320.0, 350.0]  # Right wrist directly in center of basket

    mock_kpts_inside = MagicMock()
    mock_kpts_inside.xy = kpts_inside
    mock_kpts_inside.conf = kpts_conf

    state, event = tracker.process_frame(dummy_frame, empty_dets, keypoints=mock_kpts_inside)
    assert tracker.hand_in_basket is True
    assert state == BasketState.HAND_ENTERING


def test_draw_skeleton_overlay():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    kpts = np.full((1, 17, 2), 200.0, dtype=np.float32)
    kpts[0, 9] = [320.0, 350.0]
    conf = np.ones((1, 17), dtype=np.float32)

    annotated = draw_skeleton_overlay(frame, kpts, conf)
    assert annotated.shape == (480, 640, 3)
    # Check that pixels were drawn (not completely blank black)
    assert np.count_nonzero(annotated) > 0


def test_live_webcam_model_mode_and_telegram_config():
    service = LiveWebcamService()
    res = service.update_config(
        model_mode="yolo_pose",
        precision_mode="cuda_fp16",
        enable_skeleton=True,
        telegram_chat_id="123456789",
        telegram_enabled=True,
    )
    assert res["model_mode"] == "yolo_pose"
    assert res["precision_mode"] == "cuda_fp16"
    assert res["enable_skeleton"] is True
    assert res["telegram"]["chat_id"] == "123456789"
    assert res["telegram"]["enabled"] is True


def test_telegram_api_endpoints():
    app = FastAPI()
    app.include_router(live_webcam_router)
    client = TestClient(app)

    # 1. GET /telegram/status
    res = client.get("/api/v1/live/webcam/telegram/status")
    assert res.status_code == 200
    data = res.json()
    assert "configured" in data
    assert "has_token" in data

    # 2. POST /telegram/test with mock
    with patch("app.live.telegram.telegram_notifier.send_test_message", return_value={"success": True, "message": "OK"}):
        res_test = client.post("/api/v1/live/webcam/telegram/test")
        assert res_test.status_code == 200
        assert res_test.json()["success"] is True
