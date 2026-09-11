from __future__ import annotations

import shutil
import threading
from pathlib import Path
from uuid import uuid4

from .frames import FrameService
from .media import PreparationCancelled, PreparationError, PreparationTimeout, StorageFull, prepare_clip
from .repository import AnnotationRepository


class PreparationWorker:
    def __init__(
        self,
        repository: AnnotationRepository,
        frames: FrameService,
        *,
        poll_seconds: float = 0.25,
        stall_timeout_seconds: float = 120,
        deadline_seconds: float = 3600,
    ) -> None:
        self.repository = repository
        self.frames = frames
        self.poll_seconds = poll_seconds
        self.stall_timeout_seconds = stall_timeout_seconds
        self.deadline_seconds = deadline_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="annotation-preparation", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(15, self.poll_seconds * 4))
            if self._thread.is_alive():
                raise RuntimeError("annotation preparation worker did not stop")
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def reconcile_startup(self) -> None:
        """Turn crash-left preparations into explicit failures and remove owned staging."""
        while True:
            clip = self.repository.next_clip_in_state("preparing")
            if clip is None:
                break
            self.repository.fail_preparation(clip.id, "preparation_interrupted")
        staging_root = (self.repository.database.root / "staging").resolve()
        if not staging_root.exists():
            return
        for candidate in staging_root.iterdir():
            try:
                resolved = candidate.resolve()
                resolved.relative_to(staging_root)
            except (OSError, ValueError):
                continue
            if resolved == staging_root:
                continue
            if candidate.is_dir():
                shutil.rmtree(candidate)
            elif candidate.is_file():
                candidate.unlink()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def run_once(self) -> bool:
        releasing = self.repository.next_clip_in_state("releasing")
        if releasing is not None:
            try:
                self.frames.release_generation(releasing.id)
            except BaseException:
                self.repository.fail_release(releasing.id, "prepared_release_failed")
            return True
        clip = self.repository.next_clip_in_state("preparing")
        if clip is None:
            return False
        generation = uuid4()
        staging = self.repository.database.root / "staging" / str(clip.id) / str(generation)
        try:
            source = self.frames.resolve_source_path(clip)
            if not source.is_file():
                self.repository.fail_preparation(
                    clip.id, "source_missing", source_state="missing"
                )
                return True
            metadata = self.repository.jobs.get(str(clip.source_job_id)).metadata
            prepared = prepare_clip(
                source,
                staging,
                metadata.fps_num,
                metadata.fps_den,
                metadata.sample_aspect_ratio,
                stall_timeout_seconds=self.stall_timeout_seconds,
                deadline_seconds=self.deadline_seconds,
                max_output_bytes=self.frames.available_preparation_bytes(),
                cancel_requested=self._stop.is_set,
            )
            self.frames.publish(clip.id, prepared, staging, generation)
        except FileNotFoundError:
            self.repository.fail_preparation(clip.id, "source_missing", source_state="missing")
        except PreparationTimeout:
            self.repository.fail_preparation(clip.id, "preparation_timeout")
        except PreparationCancelled:
            self.repository.fail_preparation(clip.id, "preparation_interrupted")
        except StorageFull:
            self.repository.fail_preparation(clip.id, "annotation_storage_full")
        except PreparationError:
            self.repository.fail_preparation(clip.id, "preparation_failed")
        except BaseException:
            self.repository.fail_preparation(clip.id, "preparation_failed")
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return True
