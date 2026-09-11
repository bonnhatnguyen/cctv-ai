from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class Dto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(Dto):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class RegisterClip(Dto):
    operation_id: UUID
    source_job_id: UUID


class RoiWrite(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)
    camera_setup_id: UUID
    polygon: list[Point] = Field(min_length=3, max_length=64)
    template_revision_id: UUID | None = None


class MediaView(Dto):
    frame_count: StrictInt = Field(gt=0)
    fps_num: StrictInt = Field(gt=0)
    fps_den: StrictInt = Field(gt=0)
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    sample_aspect_ratio: str


class RoiView(Dto):
    id: UUID
    revision: StrictInt = Field(ge=1)
    camera_setup_id: UUID
    polygon: list[Point]
    template_revision_id: UUID | None


class ClipView(Dto):
    schema_version: Literal[1] = 1
    id: UUID
    source_job_id: UUID
    original_name: str
    revision: StrictInt = Field(ge=0)
    preparation_state: Literal["preparing", "ready", "failed", "releasing", "released"]
    source_state: Literal["available", "missing", "hash_mismatch"]
    failure_code: str | None
    source_sha256: str | None
    media: MediaView | None
    roi: RoiView | None
    preview_url: str | None
    prepared_bytes: StrictInt | None


class CameraSetupCreate(Dto):
    operation_id: UUID
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CameraSetupView(Dto):
    id: UUID
    name: str
    revision: StrictInt = Field(ge=0)
    template: RoiView | None


class TemplateWrite(Dto):
    operation_id: UUID
    expected_setup_revision: StrictInt = Field(ge=0)
    polygon: list[Point] = Field(min_length=3, max_length=64)


class RetryPreparation(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)


class ReleasePreparedMedia(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)


class StorageView(Dto):
    used_bytes: StrictInt = Field(ge=0)
    limit_bytes: StrictInt = Field(ge=0)
    free_bytes: StrictInt = Field(ge=0)


class ErrorView(Dto):
    detail: str


class ClipListView(Dto):
    items: list[ClipView]
    next_cursor: str | None


class CameraSetupListView(Dto):
    items: list[CameraSetupView]
    next_cursor: str | None
