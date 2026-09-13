"""Unit tests for MediaPipe 21-Keypoint Hand Analysis, Gesture Recognition, Currency Detection and Action Sequence."""

from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from app.live.hand_analyzer import (
    MediaPipeHandAnalyzer,
    VietnameseCurrencyDetector,
    HandAnalysisResult,
    draw_cyber_hand_skeleton,
)
from app.live.webcam import BasketState, CashBasketTracker
from app.live.telegram import TelegramNotifier


def test_mediapipe_hand_analyzer_empty_frame():
    analyzer = MediaPipeHandAnalyzer()
    res = analyzer.analyze(np.zeros((10, 10, 3), dtype=np.uint8))
    assert isinstance(res, HandAnalysisResult)
    assert res.detected is False
    assert res.gesture == "None"
    assert res.is_pinching is False


def test_mediapipe_hand_analyzer_mock_detection():
    analyzer = MediaPipeHandAnalyzer()
    # Mock recognizer
    mock_lm = [MagicMock(x=0.5, y=0.5, z=0.0) for _ in range(21)]
    # Tip of thumb (4) and index (8) close together
    mock_lm[4].x, mock_lm[4].y = 0.50, 0.50
    mock_lm[8].x, mock_lm[8].y = 0.51, 0.51
    mock_lm[0].x, mock_lm[0].y = 0.50, 0.80  # wrist
    mock_lm[9].x, mock_lm[9].y = 0.50, 0.50  # middle MCP

    mock_rec_result = MagicMock()
    mock_rec_result.hand_landmarks = [mock_lm]
    mock_rec_result.gestures = [[MagicMock(category_name="Closed_Fist", score=0.92)]]
    mock_rec_result.handedness = [[MagicMock(category_name="Right")]]

    mock_recognizer = MagicMock()
    mock_recognizer.recognize.return_value = mock_rec_result

    with patch.object(analyzer, "_ensure_recognizer", return_value=mock_recognizer):
        crop = np.zeros((100, 100, 3), dtype=np.uint8)
        res = analyzer.analyze(crop)
        assert res.detected is True
        assert res.is_pinching is True
        assert res.is_fist is True
        assert res.handedness == "Right"
        assert len(res.landmarks) == 21


def test_vietnamese_currency_detector():
    detector = VietnameseCurrencyDetector()
    assert detector.DENOMINATION_MAP["500000"] == "500.000đ"

    # Mock YOLO predict
    mock_box = MagicMock()
    mock_box.cls = [8]
    mock_box.conf = [0.89]
    mock_box.xyxy = [np.array([10.0, 20.0, 80.0, 60.0])]

    mock_res = MagicMock()
    mock_res.boxes = [mock_box]

    mock_model = MagicMock()
    mock_model.names = {8: "500000"}
    mock_model.predict.return_value = [mock_res]

    with patch.object(detector, "_ensure_model", return_value=mock_model):
        crop = np.zeros((100, 100, 3), dtype=np.uint8)
        label, conf, box = detector.detect_currency(crop)
        assert label == "500.000đ"
        assert conf == 0.89
        assert box == (10, 20, 80, 60)


def test_draw_detected_currency():
    from app.live.hand_analyzer import draw_detected_currency
    scene = np.zeros((480, 640, 3), dtype=np.uint8)
    drawn = draw_detected_currency(
        scene=scene,
        crop_x1=100,
        crop_y1=100,
        label="20.000đ",
        confidence=0.82,
        box_crop=(10, 10, 50, 40),
    )
    assert drawn is not None
    assert np.count_nonzero(drawn) > 0


def test_cash_basket_action_sequence_withdrawal():
    tracker = CashBasketTracker()
    tracker.state = BasketState.CASH_INTERACTING
    tracker.interaction_start_time = 100.0
    tracker.interaction_pinch_occurred = True
    tracker.final_pinch_or_fist = True
    tracker.initial_pinch_or_fist = False

    # Hand leaves basket at t = 101.5 (duration 1.5s)
    with patch("time.time", return_value=101.5):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        empty_dets = MagicMock(xyxy=np.empty((0, 4)), __len__=lambda self: 0)
        # Mock hand_analyzer to return not detected
        with patch.object(tracker.hand_analyzer, "analyze", return_value=HandAnalysisResult()):
            state, event = tracker.process_frame(frame, empty_dets)
            assert state == BasketState.TRANSACTION_COMPLETE
            assert event is not None
            assert event["event_type"] == "RÚT TIỀN"
            assert event["is_pinching"] is True


def test_cash_basket_action_sequence_deposit():
    tracker = CashBasketTracker()
    tracker.state = BasketState.CASH_INTERACTING
    tracker.interaction_start_time = 100.0
    tracker.interaction_pinch_occurred = False
    tracker.initial_pinch_or_fist = True
    tracker.final_pinch_or_fist = False

    # Hand leaves basket at t = 101.5 (duration 1.5s)
    with patch("time.time", return_value=101.5):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        empty_dets = MagicMock(xyxy=np.empty((0, 4)), __len__=lambda self: 0)
        with patch.object(tracker.hand_analyzer, "analyze", return_value=HandAnalysisResult()):
            state, event = tracker.process_frame(frame, empty_dets)
            assert state == BasketState.TRANSACTION_COMPLETE
            assert event is not None
            assert event["event_type"] == "BỎ TIỀN"


def test_draw_cyber_hand_skeleton():
    scene = np.zeros((480, 640, 3), dtype=np.uint8)
    lms = [(0.5, 0.5, 0.0)] * 21
    drawn = draw_cyber_hand_skeleton(
        scene,
        landmarks=lms,
        crop_x1=100,
        crop_y1=100,
        crop_w=150,
        crop_h=150,
        is_pinching=True,
        gesture_name="Pinch",
        currency_label="500.000đ",
    )
    assert drawn is not None
    assert drawn.shape == (480, 640, 3)
    # Check that some pixels were altered (not pure black)
    assert np.count_nonzero(drawn) > 0


def test_telegram_caption_formatting_with_gesture_and_currency():
    notifier = TelegramNotifier(token="mock_token", chat_id="8269826134", enabled=True)
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        event = {
            "event_type": "RÚT TIỀN",
            "person_id": 1,
            "duration_seconds": 2.3,
            "confidence": 0.94,
            "summary": "Người #1 rút tiền từ rổ",
            "gesture": "Pinch",
            "detected_currency": "500.000đ",
        }
        notifier._dispatch_alert(b"fake_jpeg_bytes", event, "2026-09-14 04:30:00")
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        caption = call_kwargs["data"]["caption"]
        assert "RÚT TIỀN" in caption
        assert "Pinch" in caption
        assert "500.000đ" in caption
        assert "8269826134" in str(call_kwargs["data"]["chat_id"])
