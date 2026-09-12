from __future__ import annotations

import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.annotation.contracts import AssistanceModelInfo, AssistanceRunCreate
from uuid import UUID
from app.v1.api import create_app
from app.v1.settings import V1Settings


class IdleTrackingWorker:
    def __init__(self, _repository):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class ControlledAssistanceWorker:
    def __init__(self, store, *_args):
        self.store = store
        self.started = False
        self.wakes = 0

    def reconcile_startup(self):
        self.store.reconcile_running()

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def wake(self):
        self.wakes += 1

    def cancel(self, run_id, request):
        return self.store.request_cancel(run_id, request)

    def models(self):
        return [
            AssistanceModelInfo(model="dino", available=True, device="cuda:0", error_code=None),
            AssistanceModelInfo(model="mediapipe", available=False, device="cpu", error_code="asset_missing"),
        ]


def _wait_ready(client: TestClient, clip_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        clip = client.get(f"/api/v2/annotations/clips/{clip_id}").json()
        if clip["preparation_state"] != "preparing":
            return clip
        time.sleep(0.02)
    raise AssertionError("clip preparation timed out")


def _ready_clip(client: TestClient, tmp_path, make_numbered_source) -> dict:
    source = make_numbered_source(tmp_path / "assistance.mp4", frames=8)
    with source.open("rb") as handle:
        job = client.post(
            "/api/v1/jobs", files={"video": ("assist.mp4", handle, "video/mp4")}
        ).json()
    clip = client.post(
        "/api/v2/annotations/clips",
        json={"operation_id": str(uuid4()), "source_job_id": job["id"]},
    ).json()
    clip = _wait_ready(client, clip["id"])
    setup = client.post(
        "/api/v2/annotations/setups",
        json={"operation_id": str(uuid4()), "name": "Quay ho tro"},
    ).json()
    return client.put(
        f"/api/v2/annotations/clips/{clip['id']}/roi",
        json={
            "operation_id": str(uuid4()),
            "expected_clip_revision": clip["revision"],
            "camera_setup_id": setup["id"],
            "polygon": [
                {"x": 0.2, "y": 0.2}, {"x": 0.8, "y": 0.2},
                {"x": 0.8, "y": 0.8}, {"x": 0.2, "y": 0.8},
            ],
        },
    ).json()


def test_assistance_run_routes_and_lifecycle(tmp_path, make_numbered_source):
    app = create_app(
        settings=V1Settings(data_dir=tmp_path / "data"),
        worker_factory=IdleTrackingWorker,
        assistance_worker_factory=ControlledAssistanceWorker,
    )
    with TestClient(app) as client:
        assert app.state.assistance_worker.started
        models = client.get("/api/v2/annotations/assist-models")
        assert models.status_code == 200
        assert models.json()[0]["model"] == "dino"
        clip = _ready_clip(client, tmp_path, make_numbered_source)
        response = client.post(
            f"/api/v2/annotations/clips/{clip['id']}/assist-runs",
            json={
                "operation_id": str(uuid4()),
                "expected_clip_revision": clip["revision"],
                "model": "dino", "start_frame": 0, "end_frame": 7,
            },
        )
        assert response.status_code == 202
        assert "source_path" not in response.text and "model_root" not in response.text
        run = response.json()
        assert app.state.assistance_worker.wakes == 1
        assert client.get(f"/api/v2/annotations/clips/{clip['id']}/assist-runs").json()["items"][0]["id"] == run["id"]
        cancelled = client.post(
            f"/api/v2/annotations/clips/{clip['id']}/assist-runs/{run['id']}/cancel",
            json={"operation_id": str(uuid4())},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
    assert not app.state.assistance_worker.started


def test_reject_suggestion_is_idempotent_and_does_not_create_action(
    tmp_path, make_numbered_source
):
    app = create_app(
        settings=V1Settings(data_dir=tmp_path / "data"),
        worker_factory=IdleTrackingWorker,
        assistance_worker_factory=ControlledAssistanceWorker,
    )
    with TestClient(app) as client:
        clip = _ready_clip(client, tmp_path, make_numbered_source)
        run = app.state.assistance_store.create_run(
            UUID(clip["id"]),
            AssistanceRunCreate(
                operation_id=uuid4(), expected_clip_revision=clip["revision"],
                model="dino", start_frame=0, end_frame=7,
            ),
        )
        app.state.assistance_store.mark_running(run.id, scheduled_frames=2)
        app.state.assistance_store.publish(run.id, app.state.assistance_store.run_binding(run.id), [{
            "proposal_key": "one", "label": "hand_in", "action_start_frame": 1,
            "action_end_frame": 4, "view_start_frame": 0, "view_end_frame": 6,
            "crossing_estimate": 3, "crossing_bracket_start": 2,
            "crossing_bracket_end": 4, "reason": "crossing", "evidence": {},
        }])
        suggestion = client.get(
            f"/api/v2/annotations/clips/{clip['id']}/assist-suggestions"
        ).json()["items"][0]
        before = client.get(f"/api/v2/annotations/clips/{clip['id']}/actions").json()
        body = {"operation_id": str(uuid4()), "expected_clip_revision": clip["revision"]}
        url = f"/api/v2/annotations/clips/{clip['id']}/assist-suggestions/{suggestion['id']}/reject"
        first = client.post(url, json=body)
        second = client.post(url, json=body)
        assert first.status_code == 200 and second.json() == first.json()
        assert first.json()["review_state"] == "rejected"
        assert client.get(f"/api/v2/annotations/clips/{clip['id']}/actions").json() == before
