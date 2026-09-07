"""Append-only SQLAlchemy persistence records for video evidence."""

from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    rtsp_env_key: Mapped[str] = mapped_column(String(128), nullable=False)
    rois: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ObservationRecord(Base):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    track_id: Mapped[str | None] = mapped_column(String(128))
    roi_id: Mapped[str | None] = mapped_column(String(128))
    bbox: Mapped[list | None] = mapped_column(JSON)
    denomination: Mapped[int | None] = mapped_column(Integer)


class VideoEventRecord(Base):
    __tablename__ = "video_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    camera_id: Mapped[str | None] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    source_observation_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    related_track_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float)
    start_ms: Mapped[int] = mapped_column(Integer, index=True)
    end_ms: Mapped[int] = mapped_column(Integer)


class TransactionRecord(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    opened_at_ms: Mapped[int] = mapped_column(Integer, index=True)
    updated_at_ms: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    event_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
