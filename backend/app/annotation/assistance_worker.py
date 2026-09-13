from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .assistance_protocol import AssistanceChildRequest, AssistanceChildResult
from .assistance_store import AssistanceRepository, AssistanceStateConflict
from .contracts import AssistanceModelInfo, AssistanceRunCancel
from .repository import AnnotationRepository
from .frames import SourceChanged
from .settings import AnnotationSettings
from app.inference_lease import InferenceLease
from app.inference_lease import InferenceCancelled
from app.owned_process import OwnedProcess
from app.annotation_benchmark.contracts import ModelAsset
from app.annotation_benchmark.models import verify_asset


logger = logging.getLogger(__name__)


class AssistanceExecutionError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class InvalidModelOutput(RuntimeError):
    pass


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assistance_asset_hashes(settings: AnnotationSettings) -> dict[str, str]:
    if settings.assistance_model_root is None:
        return {}
    directories = {
        "dino": settings.assistance_model_root / "grounding-dino-tiny",
        "mediapipe": settings.assistance_model_root / "mediapipe-hand-landmarker",
    }
    result: dict[str, str] = {}
    for model, directory in directories.items():
        manifest = directory / "asset.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            asset = ModelAsset.model_validate(payload)
            expected = {
                "dino": "IDEA-Research/grounding-dino-tiny",
                "mediapipe": "mediapipe-hand-landmarker",
            }[model]
            if asset.model_id != expected:
                continue
            verify_asset(directory, asset)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        result[model] = hashlib.sha256(canonical).hexdigest()
    return result


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
        self._current_lock = threading.Lock()
        self._current_run_id = None
        self._current_cancel: threading.Event | None = None
        self._runtime_probe_cache: dict[tuple[str, str], tuple[bool, str | None]] = {}
        self._asset_hashes = dict(store.asset_hashes)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="annotation-assistance", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._current_lock:
            if self._current_cancel is not None:
                self._current_cancel.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(15, self.settings.assistance_poll_seconds * 4))
            if self._thread.is_alive():
                raise RuntimeError("assistance worker did not stop")
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def cancel(self, run_id, request: AssistanceRunCancel):
        result = self.store.request_cancel(run_id, request)
        with self._current_lock:
            if self._current_run_id == run_id and self._current_cancel is not None:
                self._current_cancel.set()
        self._wake.set()
        return result

    def models(self) -> list[AssistanceModelInfo]:
        python_ready = bool(
            self.settings.assistance_enabled
            and self.settings.assistance_python
            and self.settings.assistance_python.is_file()
        )
        result = []
        for model, device in (("dino", self.settings.assistance_dino_device), ("mediapipe", "cpu")):
            if self._execute_override is not None:
                try:
                    asset_ready = (self._asset_directory(model) / "asset.json").is_file()
                except FileNotFoundError:
                    asset_ready = False
            else:
                try:
                    asset_ready = (
                        self.asset_manifest_sha256(self._asset_directory(model))
                        == self._asset_hashes.get(model)
                    )
                except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                    asset_ready = False
            runtime_ready, runtime_error = (
                (True, None)
                if self._execute_override is not None or not python_ready
                else self._probe_runtime(model, device)
            )
            available = python_ready and asset_ready and runtime_ready
            if not python_ready:
                error_code = "python_missing"
            elif not asset_ready:
                error_code = "asset_missing"
            else:
                error_code = runtime_error
            result.append(AssistanceModelInfo(
                model=model, available=available, device=device,
                error_code=None if available else error_code,
            ))
        return result

    def _probe_runtime(self, model: str, device: str) -> tuple[bool, str | None]:
        key = (model, device)
        cached = self._runtime_probe_cache.get(key)
        if cached is not None:
            return cached
        python = self.settings.assistance_python
        if python is None:
            return False, "python_missing"
        if model == "dino":
            code = (
                "import sys,torch,transformers;"
                "sys.exit(3 if sys.argv[1]=='cuda:0' and not torch.cuda.is_available() else 0)"
            )
        else:
            code = "import mediapipe"
        environment = os.environ.copy()
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
        try:
            completed = subprocess.run(
                [str(python), "-c", code, device],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode == 0:
                result = (True, None)
            elif completed.returncode == 3:
                result = (False, "cuda_unavailable")
            else:
                result = (False, "runtime_missing")
        except (OSError, subprocess.TimeoutExpired):
            result = (False, "runtime_probe_failed")
        self._runtime_probe_cache[key] = result
        return result

    def reconcile_startup(self) -> None:
        self.store.reconcile_running()
        self._recover_staging()

    def _recover_staging(self) -> None:
        try:
            root = self._assistance_root()
        except AssistanceExecutionError:
            return
        for stage in (root / "staging").iterdir():
            if not stage.is_dir() or stage.is_symlink():
                continue
            target = root / "runs" / stage.name
            if target.exists():
                continue
            try:
                self._retain_stage(stage, target)
            except (AssistanceExecutionError, OSError, ValueError):
                logger.exception("could not recover assistance stage %s", stage.name)

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
    def asset_manifest_sha256(path: Path) -> str:
        payload = json.loads((path / "asset.json").read_text(encoding="utf-8"))
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _verify_asset_binding(self, model_root: Path, expected_sha256: str | None) -> None:
        if expected_sha256 is None:
            return
        try:
            payload = json.loads((model_root / "asset.json").read_text(encoding="utf-8"))
            asset = ModelAsset.model_validate(payload)
            actual_sha256 = self.asset_manifest_sha256(model_root)
            if actual_sha256 != expected_sha256:
                raise ValueError("asset manifest changed")
            verify_asset(model_root, asset)
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise AssistanceExecutionError("asset_changed", "model asset binding changed") from exc

    @staticmethod
    def _sar(value: str) -> tuple[int, int]:
        left, _, right = value.partition(":")
        return int(left), int(right or "1")

    def _request(self, run):
        private = self.annotations.get_private_clip(run.clip_id)
        clip = self.annotations.get_clip(run.clip_id)
        try:
            source = self.frames.verify_source_path(private)
        except FileNotFoundError as exc:
            raise AssistanceExecutionError("source_missing", "clip source is missing") from exc
        try:
            model_root = self._asset_directory(run.model)
        except FileNotFoundError as exc:
            raise AssistanceExecutionError("asset_missing", "model asset is missing") from exc
        self._verify_asset_binding(model_root, run.asset_sha256)
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
            model_root=model_root, device=run.device,
            stride=self.store.run_stride(run.id),
        )

    def _execute(
        self, request: AssistanceChildRequest, cancel_requested: Callable[[], bool]
    ) -> AssistanceChildResult:
        if self._execute_override is not None:
            return self._execute_override(request)
        python = self.settings.assistance_python
        if python is None or not python.is_file():
            raise AssistanceExecutionError("python_missing", "assistance Python is unavailable")
        root = self._assistance_root()
        stage = root / "staging" / str(request.run_id)
        stage.mkdir(parents=True, exist_ok=False)
        request_path = stage / "request.json"
        result_path = stage / "result.json"
        progress_path = stage / "progress.json"
        request_path.write_text(request.model_dump_json(), encoding="utf-8")
        self._ensure_artifact_capacity(stage)
        environment = os.environ.copy()
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
        command = [
            str(python), "-m", "app.annotation.assistance_child",
            "--request", str(request_path), "--result", str(result_path),
            "--progress", str(progress_path),
        ]
        try:
            with (stage / "stdout.log").open("wb") as stdout, (
                stage / "stderr.log"
            ).open("wb") as stderr, OwnedProcess(
                command, cwd=Path(__file__).resolve().parents[2], env=environment,
                stdout=stdout, stderr=stderr,
            ) as process:
                deadline = time.monotonic() + self.settings.assistance_deadline_seconds
                reported_progress = 0
                while process.poll() is None:
                    if cancel_requested():
                        raise AssistanceExecutionError("cancelled", "model process cancelled")
                    if time.monotonic() >= deadline:
                        raise AssistanceExecutionError(
                            "deadline_exceeded", "model process deadline exceeded"
                        )
                    self._ensure_artifact_capacity(stage)
                    reported_progress = self._read_progress(
                        request.run_id, progress_path, reported_progress
                    )
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
            self._ensure_artifact_capacity(stage)
            self._read_progress(request.run_id, progress_path, reported_progress)
            if process.returncode != 0 or not result_path.is_file():
                stderr_text = (stage / "stderr.log").read_text(
                    encoding="utf-8", errors="replace"
                ).casefold()
                code = "out_of_memory" if "out of memory" in stderr_text or "oom" in stderr_text else "model_process_failed"
                raise AssistanceExecutionError(code, "model process failed")
            try:
                return AssistanceChildResult.model_validate_json(result_path.read_bytes())
            except ValidationError as exc:
                raise InvalidModelOutput("model result schema is invalid") from exc
        finally:
            self._retain_stage(stage, root / "runs" / str(request.run_id))

    def _assistance_root(self) -> Path:
        if self.settings.root is None:
            raise AssistanceExecutionError("artifact_root_invalid", "annotation root is not configured")
        annotation_root = self.settings.root.resolve()
        root = annotation_root / "assistance"
        if root.exists() and (root.is_symlink() or not _is_below(root.resolve(), annotation_root)):
            raise AssistanceExecutionError("artifact_root_invalid", "assistance root escaped annotation root")
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve()
        if not _is_below(resolved, annotation_root):
            raise AssistanceExecutionError("artifact_root_invalid", "assistance root escaped annotation root")
        for name in ("staging", "runs"):
            child = root / name
            if child.exists() and (child.is_symlink() or not _is_below(child.resolve(), resolved)):
                raise AssistanceExecutionError("artifact_root_invalid", "assistance child root escaped")
            child.mkdir(exist_ok=True)
        return resolved

    def _ensure_artifact_capacity(self, stage: Path) -> None:
        root = self._assistance_root()
        if not _is_below(stage.resolve(strict=True), root / "staging"):
            raise AssistanceExecutionError(
                "artifact_root_invalid", "run stage escaped the assistance root"
            )
        used = sum(
            item.stat().st_size for item in self._stage_files(root)
        )
        if used > self.settings.assistance_output_limit_bytes:
            raise AssistanceExecutionError(
                "artifact_quota_exceeded", "assistance artifact quota exceeded"
            )
        if self.settings.root is None or shutil.disk_usage(self.settings.root).free < self.settings.free_disk_reserve_bytes:
            raise AssistanceExecutionError(
                "disk_reserve_exhausted", "annotation disk reserve is exhausted"
            )

    def _read_progress(self, run_id, progress_path: Path, previous: int) -> int:
        try:
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
            current = int(payload["processed_frames"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return previous
        if current > previous:
            try:
                self.store.update_progress(run_id, current)
            except ValueError as exc:
                raise InvalidModelOutput("model progress exceeds its schedule") from exc
            return current
        return previous

    def _retain_stage(self, stage: Path, target: Path) -> None:
        if not stage.exists():
            return
        self._bound_stage(stage)
        files = {
            str(item.relative_to(stage)).replace("\\", "/"): _sha256_file(item)
            for item in self._stage_files(stage)
        }
        manifest = stage / "artifacts.sha256.json"
        manifest.write_text(
            json.dumps({"schema_version": 1, "files": files}, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        root = self._assistance_root()
        if not _is_below(stage.resolve(), root / "staging") or not _is_below(
            target.parent.resolve(), root / "runs"
        ):
            raise AssistanceExecutionError("artifact_root_invalid", "artifact path escaped")
        if target.exists():
            raise AssistanceExecutionError("artifact_conflict", "run artifact already exists")
        stage.replace(target)

    def _bound_stage(self, stage: Path) -> None:
        self._stage_files(stage)
        limit = self.settings.assistance_output_limit_bytes
        reserve = min(64 * 1024, limit // 8)
        budget = limit - reserve
        protected = [
            item for item in (stage / "request.json", stage / "progress.json")
            if item.is_file()
        ]
        protected_bytes = sum(item.stat().st_size for item in protected)
        remaining = max(0, budget - protected_bytes)
        for name in ("result.json", "stderr.log", "stdout.log"):
            path = stage / name
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size <= remaining:
                remaining -= size
                continue
            if name == "result.json":
                path.unlink()
                continue
            with path.open("r+b") as output:
                output.truncate(remaining)
            remaining = 0

    @staticmethod
    def _stage_files(stage: Path) -> list[Path]:
        root = stage.resolve(strict=True)
        files: list[Path] = []
        for item in stage.rglob("*"):
            if item.is_symlink():
                raise AssistanceExecutionError(
                    "artifact_root_invalid", "run artifact contains a symbolic link"
                )
            if item.is_file():
                resolved = item.resolve(strict=True)
                if not _is_below(resolved, root):
                    raise AssistanceExecutionError(
                        "artifact_root_invalid", "run artifact escaped its stage"
                    )
                files.append(item)
        return files

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
        cancellation = threading.Event()
        with self._current_lock:
            self._current_run_id = run.id
            self._current_cancel = cancellation
        try:
            binding = self.store.run_binding(run.id)
            request = self._request(run)
            scheduled = list(range(run.start_frame, run.end_frame + 1, request.stride))
            cancelled = lambda: (
                self._stop.is_set() or cancellation.is_set() or self.store.is_cancelled(run.id)
            )
            with self._inference_lease.acquire(cancelled):
                self.store.mark_running(run.id, scheduled_frames=len(scheduled))
                result = self._execute(request, cancelled)
            if result.run_id != run.id or result.scheduled_frames != scheduled:
                raise InvalidModelOutput("model result schedule does not match request")
            for proposal in result.proposals:
                if proposal.segment_id != run.id or not (
                    run.start_frame <= proposal.view_span.start_frame
                    <= proposal.view_span.end_frame <= run.end_frame
                ):
                    raise InvalidModelOutput("model proposal is outside the requested interval")
                for span in (proposal.action_span, proposal.crossing_bracket):
                    if span is not None and not (
                        run.start_frame <= span.start_frame <= span.end_frame <= run.end_frame
                    ):
                        raise InvalidModelOutput("model evidence span is outside the requested interval")
                if proposal.crossing_estimate is not None and not (
                    run.start_frame <= proposal.crossing_estimate <= run.end_frame
                ):
                    raise InvalidModelOutput("model crossing estimate is outside the requested interval")
            self.store.update_progress(run.id, len(result.observed_frames))
            self.frames.verify_source_path(self.annotations.get_private_clip(run.clip_id))
            self.store.publish(
                run.id, binding,
                [self._proposal_payload(item) for item in result.proposals],
            )
        except SourceChanged:
            self._fail(run.id, "source_changed")
        except InferenceCancelled:
            self._fail(run.id, "interrupted" if self._stop.is_set() else "cancelled")
        except InvalidModelOutput:
            self._fail(run.id, "invalid_model_output")
        except AssistanceExecutionError as exc:
            self._fail(run.id, exc.error_code)
        except AssistanceStateConflict as exc:
            message = str(exc).casefold()
            if "config changed" in message:
                code = "config_changed"
            elif "stale" in message:
                code = "stale_binding"
            else:
                code = "state_conflict"
            self._fail(run.id, code)
        except MemoryError:
            self._fail(run.id, "out_of_memory")
        except Exception as exc:
            message = str(exc).casefold()
            if "deadline" in message or "timeout" in message:
                code = "deadline_exceeded"
            elif "out of memory" in message or "oom" in message:
                code = "out_of_memory"
            elif "source changed" in message:
                code = "source_changed"
            elif "cancel" in message:
                code = "cancelled"
            else:
                code = "model_process_failed"
            self._fail(run.id, code)
        finally:
            with self._current_lock:
                self._current_run_id = None
                self._current_cancel = None
        return True

    def _fail(self, run_id, error_code: str) -> None:
        try:
            self.store.fail(run_id, error_code)
        except AssistanceStateConflict:
            pass
