from __future__ import annotations

import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.v1.api import create_app
from app.v1.settings import V1Settings


class IdleTrackingWorker:
    def __init__(self, _repository):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def submit(self, _job_id):
        raise AssertionError("annotation workflow must not start tracking")


def _wait_prepared(client: TestClient, clip_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v2/annotations/clips/{clip_id}").json()
        if payload["preparation_state"] != "preparing":
            return payload
        time.sleep(0.02)
    raise AssertionError("annotation preparation did not finish")


def test_annotation_opens_imported_job_without_tracking(tmp_path, make_numbered_source):
    settings = V1Settings(
        data_dir=tmp_path / "private-data",
        model_path=tmp_path / "model.pt",
        device="cpu",
    )
    application = create_app(settings=settings, worker_factory=IdleTrackingWorker)
    upload = make_numbered_source(tmp_path / "upload.mp4", frames=8)
    with TestClient(application) as client, upload.open("rb") as handle:
        imported_response = client.post(
            "/api/v1/jobs", files={"video": ("shop.mp4", handle, "video/mp4")}
        )
        assert imported_response.status_code == 201
        imported = imported_response.json()
        response = client.post(
            "/api/v2/annotations/clips",
            json={"operation_id": str(uuid4()), "source_job_id": imported["id"]},
        )
        assert response.status_code == 201
        clip = _wait_prepared(client, response.json()["id"])
        assert clip["preparation_state"] == "ready"
        assert client.get(f"/api/v1/jobs/{imported['id']}").json()["status"] == "imported"
        frame = client.get(f"/api/v2/annotations/clips/{clip['id']}/frames/0")
        assert frame.status_code == 200
        assert frame.headers["x-frame-index"] == "0"
        assert frame.headers["x-source-sha256"] == clip["source_sha256"]
        assert frame.headers["content-type"].startswith("image/png")
        assert str(tmp_path) not in response.text + frame.text
        preview = client.get(
            f"/api/v2/annotations/clips/{clip['id']}/preview",
            headers={"Range": "bytes=0-99"},
        )
        assert preview.status_code == 206
        assert len(preview.content) == 100
        assert preview.headers["accept-ranges"] == "bytes"
        assert preview.headers["content-range"].startswith("bytes 0-99/")
        assert client.get(
            f"/api/v2/annotations/clips/{clip['id']}/preview",
            headers={"Range": "bytes=999999999-"},
        ).status_code == 416


def test_annotation_routes_validate_uuid_and_frame_range(tmp_path):
    settings = V1Settings(data_dir=tmp_path / "private-data")
    application = create_app(settings=settings, worker_factory=IdleTrackingWorker)
    with TestClient(application) as client:
        assert client.get("/api/v2/annotations/clips/not-a-uuid").status_code == 422
