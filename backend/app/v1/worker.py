"""One sequential persistent-job worker for the V1 processing pipeline."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .contracts import Progress, RunOptions, Stage
from .jobs import JobRepository, JobView
from .pipeline import process_video


logger = logging.getLogger(__name__)


class WorkerStoppedError(RuntimeError):
    pass


class _ProcessingInterrupted(RuntimeError):
    pass


class TrackingWorker:
    def __init__(
        self,
        repository: JobRepository,
        data_root: Path,
        model_path: Path | str,
        device: str,
        image_size: int,
        *,
        process: Callable = process_video,
        progress_interval_seconds: float = 0.25,
    ):
        self._repository = repository
        self._data_root = Path(data_root)
        self._options = RunOptions(str(model_path), device, image_size)
        self._process = process
        self._progress_interval = progress_interval_seconds
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._active = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._accepting = True
            recovered = self._repository.recover()
            for job_id in recovered:
                if job_id not in self._scheduled:
                    self._scheduled.add(job_id)
                    self._queue.put(job_id)
            self._thread = threading.Thread(target=self._run, name="v1-tracking-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._accepting = False
            self._stop.set()
            thread = self._thread
            self._queue.put(None)
        if thread is not None:
            thread.join()

    def submit(self, job_id: str) -> JobView:
        with self._lock:
            if not self._accepting:
                raise WorkerStoppedError("worker is not accepting jobs")
            view, _newly_queued = self._repository.enqueue(job_id)
            if view.status == "queued" and job_id not in self._scheduled:
                self._scheduled.add(job_id)
                self._queue.put(job_id)
            return view

    def wait_until_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._scheduled and not self._active:
                    return True
            time.sleep(0.01)
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                if self._stop.is_set():
                    continue
                with self._lock:
                    self._active = True
                try:
                    self._process_one(job_id)
                except Exception:
                    logger.exception("Unexpected V1 worker error for job %s", job_id)
                    self._persist_failure(job_id, "khong_the_xu_ly_video")
            finally:
                if job_id is not None:
                    with self._lock:
                        self._active = False
                        self._scheduled.discard(job_id)
                self._queue.task_done()

    def _process_one(self, job_id: str) -> None:
        private = self._repository.mark_processing(job_id)
        if private is None:
            return
        source = Path(private.source_path)
        final_output = Path(private.output_path)
        temporary_output = final_output.with_name("annotated.tmp.mp4")
        temporary_evidence = final_output.with_name("tracking.tmp.evidence.jsonl")
        final_evidence = final_output.with_name("tracking.evidence.jsonl")
        for path in (temporary_output, temporary_evidence):
            self._safe_remove(path)
        last_stage: Stage | None = None
        last_saved = 0.0

        def save_progress(progress: Progress) -> None:
            nonlocal last_stage, last_saved
            if self._stop.is_set():
                raise _ProcessingInterrupted()
            now = time.monotonic()
            if progress.stage != last_stage or now - last_saved >= self._progress_interval:
                self._repository.update_progress(job_id, progress)
                last_stage, last_saved = progress.stage, now

        published = False
        try:
            summary = self._process(
                source,
                temporary_output,
                self._options,
                save_progress,
                evidence_path=temporary_evidence,
            )
            if not temporary_output.is_file():
                raise RuntimeError("processing did not produce an output")
            os.replace(temporary_output, final_output)
            published = True
            if temporary_evidence.is_file():
                os.replace(temporary_evidence, final_evidence)
            self._repository.complete(job_id, final_output, summary)
        except _ProcessingInterrupted:
            self._safe_remove(temporary_output)
            self._safe_remove(temporary_evidence)
            if published:
                self._safe_remove(final_output)
            self._persist_failure(job_id, "xu_ly_bi_gian_doan")
        except Exception:
            logger.exception("V1 tracking job failed: %s", job_id)
            self._safe_remove(temporary_output)
            self._safe_remove(temporary_evidence)
            if published:
                self._safe_remove(final_output)
            self._persist_failure(job_id, "khong_the_xu_ly_video")

    @staticmethod
    def _safe_remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Unable to remove private temporary media for %s", path.name, exc_info=True)

    def _persist_failure(self, job_id: str, failure_code: str) -> None:
        for attempt in range(2):
            try:
                self._repository.fail(job_id, failure_code)
                return
            except Exception:
                logger.exception(
                    "Unable to persist V1 job failure (attempt %s) for %s", attempt + 1, job_id
                )
