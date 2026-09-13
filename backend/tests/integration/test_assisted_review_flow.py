from __future__ import annotations

import time
import subprocess
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image

from app.annotation.assistance_protocol import AssistanceChildResult
from app.annotation.assistance_worker import AssistanceWorker
from app.annotation.settings import AnnotationSettings
from app.annotation_benchmark.contracts import FrameSpan, Proposal
from app.v1.api import create_app
from app.v1.settings import V1Settings


class IdleTrackingWorker:
    def __init__(self, _repository):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _wait(client: TestClient, url: str, predicate, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(url).json()
        if predicate(payload):
            return payload
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {url}")


def _make_source(path, frames: int = 8):
    payload = b"".join(
        Image.new("RGB", (160, 90), (index * 20, index * 10, index * 5)).tobytes()
        for index in range(frames)
    )
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", "160x90", "-r", "25", "-i", "pipe:0", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-y", str(path),
    ], input=payload, check=True)
    return path


def test_assisted_proposal_requires_human_save_and_separate_confirmation(
    tmp_path,
):
    model_root = (tmp_path / "models").resolve()
    asset = model_root / "grounding-dino-tiny"
    asset.mkdir(parents=True)
    (asset / "asset.json").write_text("{}", encoding="utf-8")
    python = (tmp_path / "python.exe").resolve()
    python.write_bytes(b"fake")

    def assistance_factory(store, annotations, frames, settings):
        def execute(request):
            schedule = list(range(request.start_frame, request.end_frame + 1, request.stride))
            return AssistanceChildResult(
                run_id=request.run_id,
                scheduled_frames=schedule,
                observed_frames=schedule,
                proposals=[Proposal(
                    proposal_id=uuid4(), segment_id=uuid4(), local_track_id=1,
                    label="hand_in", action_span=FrameSpan(start_frame=1, end_frame=5),
                    view_span=FrameSpan(start_frame=0, end_frame=6),
                    crossing_estimate=3,
                    crossing_bracket=FrameSpan(start_frame=2, end_frame=4),
                    reason="crossing",
                )],
            )

        return AssistanceWorker(store, annotations, frames, settings, execute=execute)

    annotation_settings = AnnotationSettings(
        root=(tmp_path / "data" / "annotations").resolve(),
        assistance_python=python, assistance_model_root=model_root,
        assistance_stride=1, assistance_poll_seconds=0.01,
    )
    app = create_app(
        settings=V1Settings(data_dir=tmp_path / "data"),
        worker_factory=IdleTrackingWorker,
        annotation_settings=annotation_settings,
        assistance_worker_factory=assistance_factory,
    )
    source = _make_source(tmp_path / "flow.mp4", frames=8)
    with TestClient(app) as client, source.open("rb") as handle:
        job = client.post(
            "/api/v1/jobs", files={"video": ("flow.mp4", handle, "video/mp4")}
        ).json()
        registered = client.post(
            "/api/v2/annotations/clips",
            json={"operation_id": str(uuid4()), "source_job_id": job["id"]},
        ).json()
        clip_url = f"/api/v2/annotations/clips/{registered['id']}"
        clip = _wait(client, clip_url, lambda item: item["preparation_state"] == "ready")
        setup = client.post(
            "/api/v2/annotations/setups",
            json={"operation_id": str(uuid4()), "name": "Quay flow"},
        ).json()
        clip = client.put(f"{clip_url}/roi", json={
            "operation_id": str(uuid4()), "expected_clip_revision": clip["revision"],
            "camera_setup_id": setup["id"],
            "polygon": [{"x": .2, "y": .2}, {"x": .8, "y": .2}, {"x": .8, "y": .8}],
        }).json()

        run = client.post(f"{clip_url}/assist-runs", json={
            "operation_id": str(uuid4()), "expected_clip_revision": clip["revision"],
            "model": "dino", "start_frame": 0, "end_frame": 7,
        }).json()
        run_url = f"{clip_url}/assist-runs/{run['id']}"
        _wait(client, run_url, lambda item: item["status"] == "succeeded")
        queue = client.get(f"{clip_url}/assist-suggestions").json()["items"]
        assert len(queue) == 1
        assert client.get(f"{clip_url}/actions").json()["annotations"] == []

        workspace = client.post(f"{clip_url}/interactions", json={
            "operation_id": str(uuid4()), "expected_clip_revision": clip["revision"],
            "hand": "right",
        }).json()
        saved = client.post(f"{clip_url}/actions", json={
            "operation_id": str(uuid4()),
            "expected_clip_revision": workspace["clip_revision"],
            "suggestion_id": queue[0]["id"],
            "interaction_id": workspace["interactions"][0]["id"],
            "label": "hand_in", "start_frame": 1, "end_frame": 5,
            "crossing_frame": 3, "object_kind": "unknown", "visibility": "clear",
            "uncertain_labels": [], "unclear_reason": None,
        }).json()
        action = saved["annotations"][0]
        assert action["review_state"] == "draft"
        assert saved["review_coverage"] == []
        assert app.state.assistance_store.get_suggestion(UUID(queue[0]["id"])).review_state == "accepted"

        confirmed = client.post(f"{clip_url}/actions/{action['id']}/confirm", json={
            "operation_id": str(uuid4()),
            "expected_clip_revision": saved["clip_revision"],
            "expected_annotation_revision": action["revision"],
        }).json()
        assert confirmed["annotations"][0]["review_state"] == "confirmed"
