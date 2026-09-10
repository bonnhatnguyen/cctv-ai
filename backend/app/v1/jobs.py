"""Persistent job state and public, path-free response models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Float, Integer, String, Text, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .contracts import JobStatus, Progress, RunSummary, Stage, VideoMetadata


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobNotFoundError(LookupError):
    pass


class JobConflictError(RuntimeError):
    pass


class Base(DeclarativeBase):
    pass


class JobRecord(Base):
    __tablename__ = "v1_tracking_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    processed_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_frames_estimate: Mapped[int | None] = mapped_column(Integer)
    tracking_percent: Mapped[float | None] = mapped_column(Float)
    summary_json: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    output_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40))
    finished_at: Mapped[str | None] = mapped_column(String(40))


class JobView(BaseModel):
    id: str
    original_name: str
    status: JobStatus
    stage: Stage
    metadata: VideoMetadata
    processed_frames: int
    total_frames_estimate: int | None
    tracking_percent: float | None
    summary: RunSummary | None
    failure_code: str | None
    source_url: str
    result_url: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class PrivateJob:
    id: str
    status: JobStatus
    source_path: str
    output_path: str


def _view(record: JobRecord) -> JobView:
    summary = RunSummary(**json.loads(record.summary_json)) if record.summary_json else None
    return JobView(
        id=record.id,
        original_name=record.original_name,
        status=JobStatus(record.status),
        stage=Stage(record.stage),
        metadata=VideoMetadata(**json.loads(record.metadata_json)),
        processed_frames=record.processed_frames,
        total_frames_estimate=record.total_frames_estimate,
        tracking_percent=record.tracking_percent,
        summary=summary,
        failure_code=record.failure_code,
        source_url=f"/api/v1/jobs/{record.id}/source",
        result_url=f"/api/v1/jobs/{record.id}/result" if record.status == JobStatus.READY else None,
        created_at=datetime.fromisoformat(record.created_at),
        started_at=datetime.fromisoformat(record.started_at) if record.started_at else None,
        finished_at=datetime.fromisoformat(record.finished_at) if record.finished_at else None,
    )


class JobRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    def create_imported(
        self, job_id: str, original_name: str, source: Path, metadata: VideoMetadata
    ) -> JobView:
        record = JobRecord(
            id=job_id,
            original_name=original_name,
            status=JobStatus.IMPORTED,
            stage=Stage.IMPORTED,
            metadata_json=json.dumps(asdict(metadata)),
            processed_frames=0,
            total_frames_estimate=metadata.frame_count_estimate,
            tracking_percent=None,
            summary_json=None,
            failure_code=None,
            source_path=str(Path(source).resolve()),
            output_path=str((Path(source).parent / "annotated.mp4").resolve()),
            created_at=_now(),
            started_at=None,
            finished_at=None,
        )
        with self._sessions() as session:
            session.add(record)
            session.commit()
            return _view(record)

    def get(self, job_id: str) -> JobView:
        with self._sessions() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            return _view(record)

    def get_private(self, job_id: str) -> PrivateJob:
        with self._sessions() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            return PrivateJob(record.id, JobStatus(record.status), record.source_path, record.output_path)

    def enqueue(self, job_id: str) -> tuple[JobView, bool]:
        with self._sessions() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            if record.status in {JobStatus.READY, JobStatus.FAILED}:
                raise JobConflictError(job_id)
            if record.status in {JobStatus.QUEUED, JobStatus.PROCESSING}:
                return _view(record), False
            result = session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status == JobStatus.IMPORTED)
                .values(status=JobStatus.QUEUED, stage=Stage.QUEUED)
            )
            session.commit()
            record = session.get(JobRecord, job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            return _view(record), bool(result.rowcount)

    def mark_processing(self, job_id: str) -> PrivateJob | None:
        with self._sessions() as session:
            result = session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status == JobStatus.QUEUED)
                .values(status=JobStatus.PROCESSING, stage=Stage.LOADING, started_at=_now())
            )
            session.commit()
            if not result.rowcount:
                return None
            record = session.get(JobRecord, job_id)
            assert record is not None
            return PrivateJob(record.id, JobStatus(record.status), record.source_path, record.output_path)

    def update_progress(self, job_id: str, progress: Progress) -> None:
        stage = Stage.VALIDATING if progress.stage == Stage.READY else progress.stage
        percent: float | None = None
        if progress.processed_frames > 0 and progress.total_frames_estimate:
            percent = min(99.9, round(progress.processed_frames * 100 / progress.total_frames_estimate, 1))
        with self._sessions() as session:
            session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status == JobStatus.PROCESSING)
                .values(
                    stage=stage,
                    processed_frames=progress.processed_frames,
                    total_frames_estimate=progress.total_frames_estimate,
                    tracking_percent=percent,
                )
            )
            session.commit()

    def complete(self, job_id: str, output: Path, summary: RunSummary) -> JobView:
        finished = _now()
        with self._sessions() as session:
            result = session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status == JobStatus.PROCESSING)
                .values(
                    status=JobStatus.READY,
                    stage=Stage.READY,
                    processed_frames=summary.processed_frames,
                    tracking_percent=100.0,
                    summary_json=json.dumps(asdict(summary)),
                    failure_code=None,
                    output_path=str(Path(output).resolve()),
                    finished_at=finished,
                )
            )
            session.commit()
            if not result.rowcount:
                raise JobConflictError(job_id)
            record = session.get(JobRecord, job_id)
            assert record is not None
            return _view(record)

    def fail(self, job_id: str, failure_code: str) -> None:
        with self._sessions() as session:
            session.execute(
                update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status.in_([JobStatus.QUEUED, JobStatus.PROCESSING]))
                .values(
                    status=JobStatus.FAILED,
                    stage=Stage.FAILED,
                    failure_code=failure_code,
                    finished_at=_now(),
                )
            )
            session.commit()

    def recover(self) -> list[str]:
        with self._sessions() as session:
            session.execute(
                update(JobRecord)
                .where(JobRecord.status == JobStatus.PROCESSING)
                .values(
                    status=JobStatus.FAILED,
                    stage=Stage.FAILED,
                    failure_code="xu_ly_bi_gian_doan",
                    finished_at=_now(),
                )
            )
            session.commit()
            return list(session.scalars(
                select(JobRecord.id).where(JobRecord.status == JobStatus.QUEUED).order_by(JobRecord.created_at)
            ))

    def latest_completed_summary(self) -> RunSummary | None:
        with self._sessions() as session:
            payload = session.scalar(
                select(JobRecord.summary_json)
                .where(JobRecord.status == JobStatus.READY)
                .order_by(JobRecord.finished_at.desc())
                .limit(1)
            )
        return RunSummary(**json.loads(payload)) if payload else None
