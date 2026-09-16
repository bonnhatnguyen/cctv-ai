"""Stable, validated boundaries for video evidence and review workflows."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionStatus(StrEnum):
    OPEN = "open"
    OBSERVING = "observing"
    PARTIALLY_MATCHED = "partially_matched"
    CLOSED = "closed"
    INSUFFICIENT_OBSERVATION = "insufficient_observation"
    REVIEW_REQUIRED = "review_required"


class CameraHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class EventKind(StrEnum):
    GOODS_HANDOVER = "goods_handover"
    CASH_RECEIVED = "cash_received"
    CASH_REMOVED_FROM_BASKET = "cash_removed_from_basket"
    CASH_RETURNED_TO_BASKET = "cash_returned_to_basket"
    CASH_HANDED_TO_CUSTOMER = "cash_handed_to_customer"
    CASH_OCCLUDED = "cash_occluded"
    CASH_LOST = "cash_lost"
    CAMERA_DEGRADED = "camera_degraded"


class ReviewOutcome(StrEnum):
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    INSUFFICIENT_DATA = "insufficient_data"


BBox = Annotated[tuple[float, float, float, float], Field(description="normalized x1, y1, x2, y2")]


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)
    camera_id: str = Field(min_length=1)
    timestamp_ms: int = Field(ge=0, description="UTC Unix epoch milliseconds")
    kind: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    track_id: str | None = None
    roi_id: str | None = None
    bbox: BBox | None = None
    denomination: int | None = Field(default=None, gt=0)

    @field_validator("bbox")
    @classmethod
    def bbox_is_normalized(cls, value: BBox | None) -> BBox | None:
        if value is None:
            return value
        x1, y1, x2, y2 = value
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("bbox must be normalized with x1 < x2 and y1 < y2")
        return value


class VideoEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str | None = None
    kind: EventKind
    source_observation_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    camera_id: str | None = None
    related_track_ids: tuple[str, ...] = ()

    @field_validator("end_ms")
    @classmethod
    def end_is_not_before_start(cls, value: int, info) -> int:
        if "start_ms" in info.data and value < info.data["start_ms"]:
            raise ValueError("end_ms must not precede start_ms")
        return value


class Transaction(BaseModel):
    id: str | None = None
    status: TransactionStatus = TransactionStatus.OPEN
    opened_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    event_ids: tuple[str, ...] = ()
    reason: str | None = None


class CameraConfig(BaseModel):
    id: str = Field(min_length=1)
    rtsp_env_key: str = Field(min_length=1)
    timezone: str = Field(min_length=1, description="IANA timezone for display only")
    rois: dict[str, tuple[tuple[float, float], ...]] = Field(default_factory=dict)


class RuleConfig(BaseModel):
    correlation_window_ms: int = Field(default=15_000, gt=0)
    destination_window_ms: int = Field(default=30_000, gt=0)
    minimum_consecutive_frames: int = Field(default=2, ge=1)
    high_evidence_confidence: float = Field(default=0.9, ge=0, le=1)
    denomination_confidence: float = Field(default=0.95, ge=0, le=1)


class CaseOutcome(BaseModel):
    case_id: str
    status: TransactionStatus
