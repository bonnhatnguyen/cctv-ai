"""Unit tests for live webcam, CCTV RTSP, cash basket monitoring, and evidence audit."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.live.api import live_router, router as live_webcam_router
from app.live.shutdown import router as shutdown_router
from app.live.webcam import BasketState, CashBasketTracker, EvidenceManager, LiveWebcamService, webcam_service


def test_live_webcam_service_status_when_stopped():
    service = LiveWebcamService()
    service._basket_tracker.hand_in_basket = True
    service.stop()
    status = service.status()
    assert status["running"] is False
    assert status["hand_in_basket"] is False
    assert status["basket_state"] == BasketState.IDLE
    assert status["people_count"] == 0
    assert status["fps"] == 0.0
    assert "basket_state" in status
    assert "evidence_count" in status


def test_live_webcam_service_start_failure_handled():
    service = LiveWebcamService(source=999)
    with patch.object(service, "_open_camera", return_value=None):
        status = service.start()
        assert status["running"] is False
        assert "Không thể mở nguồn camera" in status["last_error"]
    service.stop()


def test_live_webcam_supervision_config_toggle():
    service = LiveWebcamService()
    assert service.enable_traces is False
    assert service.box_style == "corner"
    assert service.enable_zone is True
    assert service.enable_basket is True

    # Toggle
    res = service.update_config(enable_traces=False, box_style="box", enable_zone=False, enable_basket=False, flip_h=True, flip_v=True)
    assert res["enable_traces"] is False
    assert res["box_style"] == "box"
    assert res["enable_zone"] is False
    assert res["enable_basket"] is False
    assert res["flip_h"] is True
    assert res["flip_v"] is True

    # Revert
    res2 = service.update_config(enable_traces=True, box_style="corner", enable_zone=True, enable_basket=True, flip_h=False, flip_v=False)
    assert res2["enable_traces"] is True
    assert res2["box_style"] == "corner"
    assert res2["enable_zone"] is True
    assert res2["enable_basket"] is True
    assert res2["flip_h"] is False
    assert res2["flip_v"] is False


def test_evidence_manager_save_list_and_clear(tmp_path):
    em = EvidenceManager(storage_dir=tmp_path)
    assert em.count() == 0

    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    saved = em.save_evidence(
        frame=dummy_frame,
        event_type="RÚT TIỀN",
        person_id=1,
        confidence=0.92,
        duration_seconds=3.2,
        summary="Test rút tiền",
    )
    assert saved["id"].startswith("ev_")
    assert saved["event_type"] == "RÚT TIỀN"
    assert em.count() == 1

    items = em.list_evidence()
    assert len(items) == 1
    assert items[0]["id"] == saved["id"]

    img_p = em.get_image_path(saved["filename"])
    assert img_p is not None and img_p.is_file()

    cleared = em.clear_all()
    assert cleared == 2  # 1 jpg + 1 json
    assert em.count() == 0


def test_cash_basket_tracker_state_transitions():
    tracker = CashBasketTracker()
    assert tracker.state == BasketState.IDLE
    assert tracker.hand_in_basket is False

    # Custom ROI
    new_roi = [[100, 100], [300, 100], [300, 300], [100, 300]]
    tracker.set_roi(new_roi)
    assert len(tracker.polygon) == 4


def test_api_webcam_basket_and_evidence_endpoints(tmp_path):
    app = FastAPI()
    app.include_router(live_webcam_router)
    app.include_router(live_router)
    app.include_router(shutdown_router)
    client = TestClient(app)

    # 1. Get status
    resp = client.get("/api/v1/live/webcam/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "running" in data
    assert "device_name" in data
    assert "basket_state" in data

    # 2. Update config
    resp = client.post("/api/v1/live/webcam/config", json={
        "enable_traces": False,
        "box_style": "box",
        "enable_zone": True,
        "enable_basket": True,
    })
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["enable_traces"] is False
    assert cfg["box_style"] == "box"

    # 3. Update Basket ROI
    resp = client.post("/api/v1/live/webcam/basket/roi", json={
        "points": [[150, 200], [400, 200], [450, 400], [100, 400]],
    })
    assert resp.status_code == 200
    assert resp.json()["basket_roi"] == [[150, 200], [400, 200], [450, 400], [100, 400]]

    # 4. List evidence
    resp = client.get("/api/v1/live/evidence/list")
    assert resp.status_code == 200
    assert "items" in resp.json()

    # 5. Clear evidence
    resp = client.delete("/api/v1/live/evidence")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"

    # 6. Stop webcam
    resp = client.post("/api/v1/live/webcam/stop")
    assert resp.status_code == 200
    assert resp.json()["running"] is False

    # 7. Shutdown endpoint
    with patch("os._exit"):
        resp = client.post("/api/v1/system/shutdown")
        assert resp.status_code == 200
        assert resp.json()["status"] == "shutting_down"


def test_live_webcam_config_persistence(tmp_path, monkeypatch):
    import json
    from app.live import webcam as wc_mod

    cfg_file = tmp_path / "live_config.json"
    monkeypatch.setattr(wc_mod, "_resolve_config_path", lambda: cfg_file)

    service = wc_mod.LiveWebcamService()
    new_points = [[110, 110], [310, 110], [310, 310], [110, 310]]
    service.set_basket_roi(new_points)

    assert cfg_file.exists()
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["basket_roi"] == new_points

    # Verify reload on new service instance
    service2 = wc_mod.LiveWebcamService()
    assert service2._basket_tracker.polygon.tolist() == new_points


def test_simulated_video_endpoints_and_feature_flag(tmp_path, monkeypatch):
    import io
    import cv2
    from app.live import api as api_mod

    # Redirect SIMULATED_VIDEOS_DIR to tmp_path
    monkeypatch.setattr(api_mod, "SIMULATED_VIDEOS_DIR", tmp_path)

    app = FastAPI()
    app.include_router(live_router)
    app.include_router(live_webcam_router)
    client = TestClient(app)

    # 1. Test Feature Flag config toggle
    resp = client.post("/api/v1/live/config", json={"enable_video_simulation": False, "loop_video": False})
    assert resp.status_code == 200
    assert resp.json()["enable_video_simulation"] is False
    assert resp.json()["loop_video"] is False

    resp = client.post("/api/v1/live/config", json={"enable_video_simulation": True, "loop_video": True})
    assert resp.status_code == 200
    assert resp.json()["enable_video_simulation"] is True
    assert resp.json()["loop_video"] is True

    # 2. Create a dummy mini video in memory
    dummy_video_path = tmp_path / "sample_cashier.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(dummy_video_path), fourcc, 10.0, (320, 240))
    for _ in range(5):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        out.write(frame)
    out.release()

    video_bytes = dummy_video_path.read_bytes()

    # 3. Upload simulated video
    upload_resp = client.post(
        "/api/v1/live/video/upload",
        files={"file": ("sample_cashier.mp4", io.BytesIO(video_bytes), "video/mp4")},
    )
    assert upload_resp.status_code == 200
    upload_data = upload_resp.json()
    assert "video_id" in upload_data
    assert upload_data["filename"] == "sample_cashier.mp4"
    assert upload_data["width"] == 320
    assert upload_data["height"] == 240
    video_id = upload_data["video_id"]
    filepath = upload_data["filepath"]

    # 4. List simulated videos
    list_resp = client.get("/api/v1/live/video/list")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) >= 1
    assert any(it["video_id"] == video_id for it in items)

    # 5. Start webcam with video file source
    start_resp = client.post(f"/api/v1/live/webcam/start?source={filepath}&loop=true")
    assert start_resp.status_code == 200
    status_data = start_resp.json()
    assert status_data["is_simulated_video"] is True
    assert status_data["loop_video"] is True

    # Stop
    client.post("/api/v1/live/webcam/stop")

    # 6. Delete simulated video
    del_resp = client.delete(f"/api/v1/live/video/{video_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Verify deleted from list
    list_resp2 = client.get("/api/v1/live/video/list")
    assert not any(it["video_id"] == video_id for it in list_resp2.json()["items"])


