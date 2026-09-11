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
        health = client.get("/api/v2/annotations/health").json()
        assert health["schema_version"] == 1
        assert health["database_schema_version"] == 2


def _open_action_workspace(client: TestClient, tmp_path, make_numbered_source) -> dict:
    upload = make_numbered_source(tmp_path / "action-upload.mp4", frames=8)
    with upload.open("rb") as handle:
        imported = client.post(
            "/api/v1/jobs", files={"video": ("actions.mp4", handle, "video/mp4")}
        ).json()
    registered = client.post(
        "/api/v2/annotations/clips",
        json={"operation_id": str(uuid4()), "source_job_id": imported["id"]},
    ).json()
    clip = _wait_prepared(client, registered["id"])
    setup = client.post(
        "/api/v2/annotations/setups",
        json={"operation_id": str(uuid4()), "name": "Quầy API"},
    ).json()
    response = client.put(
        f"/api/v2/annotations/clips/{clip['id']}/roi",
        json={
            "operation_id": str(uuid4()),
            "expected_clip_revision": clip["revision"],
            "camera_setup_id": setup["id"],
            "polygon": [{"x": 0.2, "y": 0.2}, {"x": 0.7, "y": 0.2}, {"x": 0.7, "y": 0.7}],
        },
    )
    assert response.status_code == 200
    return response.json()


def test_action_api_workspace_mutations_and_idempotency(tmp_path, make_numbered_source):
    settings = V1Settings(data_dir=tmp_path / "private-data")
    application = create_app(settings=settings, worker_factory=IdleTrackingWorker)
    with TestClient(application) as client:
        clip = _open_action_workspace(client, tmp_path, make_numbered_source)
        workspace_url = f"/api/v2/annotations/clips/{clip['id']}/actions"
        empty = client.get(workspace_url)
        assert empty.status_code == 200
        assert empty.json()["annotations"] == []

        interaction_request = {
            "operation_id": str(uuid4()),
            "expected_clip_revision": clip["revision"],
            "hand": "right",
        }
        interaction_response = client.post(
            f"/api/v2/annotations/clips/{clip['id']}/interactions",
            json=interaction_request,
        )
        assert interaction_response.status_code == 201
        workspace = interaction_response.json()
        interaction = workspace["interactions"][0]

        operation_id = str(uuid4())
        action_request = {
            "operation_id": operation_id,
            "expected_clip_revision": workspace["clip_revision"],
            "interaction_id": interaction["id"],
            "label": "hand_in",
            "start_frame": 1,
            "end_frame": 4,
            "crossing_frame": 2,
            "object_kind": "unknown",
            "visibility": "clear",
            "uncertain_labels": [],
            "unclear_reason": None,
        }
        created = client.post(workspace_url, json=action_request)
        assert created.status_code == 201
        assert client.post(workspace_url, json=action_request).json() == created.json()
        action = created.json()["annotations"][0]

        confirmed = client.post(
            f"{workspace_url}/{action['id']}/confirm",
            json={
                "operation_id": str(uuid4()),
                "expected_clip_revision": created.json()["clip_revision"],
                "expected_annotation_revision": action["revision"],
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["annotations"][0]["review_state"] == "confirmed"

        stale = dict(action_request, operation_id=str(uuid4()))
        response = client.post(workspace_url, json=stale)
        assert response.status_code == 409
        assert response.json()["detail"] == "annotation_conflict"
        assert str(tmp_path) not in response.text

        malformed = dict(
            action_request,
            operation_id=str(uuid4()),
            expected_clip_revision=confirmed.json()["clip_revision"],
            label="take_out",
        )
        assert client.post(workspace_url, json=malformed).status_code == 422

        overlap = client.post(
            workspace_url,
            json={
                "operation_id": str(uuid4()),
                "expected_clip_revision": confirmed.json()["clip_revision"],
                "interaction_id": interaction["id"],
                "label": "unclear",
                "start_frame": 2,
                "end_frame": 3,
                "crossing_frame": None,
                "object_kind": "unknown",
                "visibility": "occluded",
                "uncertain_labels": ["hand_in"],
                "unclear_reason": "occlusion",
            },
        )
        assert overlap.status_code == 409
        assert overlap.json()["detail"] == {
            "code": "annotation_conflict",
            "conflicting_annotation_id": action["id"],
        }

        deleted = client.post(
            f"{workspace_url}/{action['id']}/delete",
            json={
                "operation_id": str(uuid4()),
                "expected_clip_revision": confirmed.json()["clip_revision"],
                "expected_annotation_revision": confirmed.json()["annotations"][0]["revision"],
            },
        )
        assert deleted.status_code == 200
        assert deleted.json()["annotations"][0]["deleted"] is True
        restored = client.post(
            f"{workspace_url}/{action['id']}/restore",
            json={
                "operation_id": str(uuid4()),
                "expected_clip_revision": deleted.json()["clip_revision"],
                "expected_annotation_revision": deleted.json()["annotations"][0]["revision"],
            },
        )
        assert restored.status_code == 200
        assert restored.json()["annotations"][0]["deleted"] is False
        updated_body = dict(
            action_request,
            operation_id=str(uuid4()),
            expected_clip_revision=restored.json()["clip_revision"],
            expected_annotation_revision=restored.json()["annotations"][0]["revision"],
            end_frame=3,
        )
        updated = client.put(
            f"{workspace_url}/{action['id']}", json=updated_body
        )
        assert updated.status_code == 200
        assert updated.json()["annotations"][0]["end_frame"] == 3
        coverage = client.post(
            f"/api/v2/annotations/clips/{clip['id']}/review-coverage",
            json={
                "operation_id": str(uuid4()),
                "expected_clip_revision": updated.json()["clip_revision"],
                "start_frame": 0,
                "end_frame": 7,
                "reviewed_labels": ["hand_out"],
            },
        )
        assert coverage.status_code == 201
        assert coverage.json()["review_coverage"][0]["reviewed_labels"] == ["hand_out"]
