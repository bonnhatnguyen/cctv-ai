from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .assistance_protocol import AssistanceChildRequest, AssistanceChildResult
from .assistance_store import AssistanceRepository
from .repository import AnnotationRepository
from .settings import AnnotationSettings
from app.inference_lease import InferenceLease


class AssistanceWorker:
    def __init__(
        self,
        store: AssistanceRepository,
        annotations: AnnotationRepository,
        frames,
        settings: AnnotationSettings,
        *,
        execute: Callable[[AssistanceChildRequest], AssistanceChildResult] | None = None,
        inference_lease: InferenceLease | None = None,
    ) -> None:
        self.store = store
        self.annotations = annotations
        self.frames = frames
        self.settings = settings
        self._execute_override = execute
        self._inference_lease = inference_lease or InferenceLease()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="annotation-assistance", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(15, self.settings.assistance_poll_seconds * 4))
            if self._thread.is_alive():
                raise RuntimeError("assistance worker did not stop")
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def reconcile_startup(self) -> None:
        self.store.reconcile_running()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._wake.wait(self.settings.assistance_poll_seconds)
            self._wake.clear()

    def _asset_directory(self, model: str) -> Path:
        if self.settings.assistance_model_root is None:
            raise FileNotFoundError("assistance model root is not configured")
        name = "grounding-dino-tiny" if model == "dino" else "mediapipe-hand-landmarker"
        path = (self.settings.assistance_model_root / name).resolve()
        if not (path / "asset.json").is_file() and self._execute_override is None:
            raise FileNotFoundError("assistance model asset is unavailable")
        return path

    @staticmethod
    def _sar(value: str) -> tuple[int, int]:
        left, _, right = value.partition(":")
        return int(left), int(right or "1")

    def _request(self, run):
        private = self.annotations.get_private_clip(run.clip_id)
        clip = self.annotations.get_clip(run.clip_id)
        source = self.frames.resolve_source_path(private)
        sar_num, sar_den = self._sar(private.media.sample_aspect_ratio)
        return AssistanceChildRequest(
            run_id=run.id, clip_id=run.clip_id, source_job_id=private.source_job_id,
            source_path=source, source_sha256=run.source_sha256,
            roi_revision_id=run.roi_revision_id,
            polygon=[(point.x, point.y) for point in clip.roi.polygon],
            frame_count=private.media.frame_count, width=private.media.width,
            height=private.media.height, fps_num=private.media.fps_num,
            fps_den=private.media.fps_den, sar_num=sar_num, sar_den=sar_den,
            start_frame=run.start_frame, end_frame=run.end_frame, model=run.model,
            model_root=self._asset_directory(run.model), device=run.device,
            stride=self.settings.assistance_stride,
        )

    def _execute(self, request: AssistanceChildRequest) -> AssistanceChildResult:
        if self._execute_override is not None:
            return self._execute_override(request)
        python = self.settings.assistance_python
        if python is None or not python.is_file():
            raise FileNotFoundError("assistance Python is unavailable")
        root = (self.settings.root / "assistance").resolve()
        stage = root / "staging" / str(request.run_id)
        stage.mkdir(parents=True, exist_ok=False)
        request_path = stage / "request.json"
        result_path = stage / "result.json"
        request_path.write_text(request.model_dump_json(), encoding="utf-8")
        environment = os.environ.copy()
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
        try:
            completed = subprocess.run(
                [str(python), "-m", "app.annotation.assistance_child",
                 "--request", str(request_path), "--result", str(result_path)],
                cwd=Path(__file__).resolve().parents[2],
                env=environment, capture_output=True, timeout=self.settings.assistance_deadline_seconds,
                check=False,
            )
            if completed.returncode != 0 or not result_path.is_file():
                raise RuntimeError("model process failed")
            return AssistanceChildResult.model_validate_json(result_path.read_bytes())
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _proposal_payload(proposal) -> dict:
        return {
            "proposal_key": str(proposal.proposal_id), "label": proposal.label,
            "action_start_frame": proposal.action_span.start_frame if proposal.action_span else None,
            "action_end_frame": proposal.action_span.end_frame if proposal.action_span else None,
            "view_start_frame": proposal.view_span.start_frame,
            "view_end_frame": proposal.view_span.end_frame,
            "crossing_estimate": proposal.crossing_estimate,
            "crossing_bracket_start": proposal.crossing_bracket.start_frame if proposal.crossing_bracket else None,
            "crossing_bracket_end": proposal.crossing_bracket.end_frame if proposal.crossing_bracket else None,
            "reason": proposal.reason, "evidence": {},
        }

    def run_once(self) -> bool:
        run = self.store.next_queued_run()
        if run is None:
            return False
        try:
            request = self._request(run)
            scheduled = list(range(run.start_frame, run.end_frame + 1, request.stride))
            with self._inference_lease.acquire(self._stop.is_set):
                self.store.mark_running(run.id, scheduled_frames=len(scheduled))
                result = self._execute(request)
            if result.run_id != run.id:
                raise RuntimeError("model result belongs to another run")
            self.store.publish(
                run.id, self.store.run_binding(run.id),
                [self._proposal_payload(item) for item in result.proposals],
            )
        except BaseException:
            self.store.fail(run.id, "model_process_failed")
        return True
