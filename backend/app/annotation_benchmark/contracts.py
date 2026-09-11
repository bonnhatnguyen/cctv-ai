from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class FrozenDto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FrameSpan(FrozenDto):
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_inclusive_bounds(self) -> "FrameSpan":
        if self.end_frame < self.start_frame:
            raise ValueError("end_frame must be greater than or equal to start_frame")
        return self


class SelectionItem(FrozenDto):
    segment_id: UUID
    clip_id: UUID
    span: FrameSpan
    partition: Literal["tuning", "evaluation", "exploratory"]
    parent_recording_id: str | None = None
    recording_days: tuple[str, ...] = ()
    provenance_confirmed: bool = False
    scenario_tags: tuple[str, ...] = Field(min_length=1)


class ReferenceEvent(FrozenDto):
    event_id: UUID
    label: Literal["hand_in", "hand_out"]
    span: FrameSpan
    crossing_frame: StrictInt = Field(ge=0)
    revision: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_crossing_frame(self) -> "ReferenceEvent":
        if not self.span.start_frame <= self.crossing_frame <= self.span.end_frame:
            raise ValueError("crossing_frame must lie inside the event span")
        return self


class FrozenSegment(FrozenDto):
    selection: SelectionItem
    source_job_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    clip_revision: StrictInt = Field(ge=0)
    roi_revision_id: UUID
    polygon: tuple[tuple[float, float], ...] = Field(min_length=3, max_length=64)
    guideline_version: Literal[1] = 1
    frame_count: StrictInt = Field(gt=0)
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    fps_num: StrictInt = Field(gt=0)
    fps_den: StrictInt = Field(gt=0)
    sar_num: StrictInt = Field(gt=0)
    sar_den: StrictInt = Field(gt=0)
    events: tuple[ReferenceEvent, ...] = ()
    coverage: dict[str, tuple[FrameSpan, ...]] = Field(default_factory=dict)
    ignored: dict[str, tuple[FrameSpan, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_segment_binding(self) -> "FrozenSegment":
        if self.selection.span.end_frame >= self.frame_count:
            raise ValueError("selection span exceeds source frame count")
        for event in self.events:
            if not (
                self.selection.span.start_frame <= event.span.start_frame
                and event.span.end_frame <= self.selection.span.end_frame
            ):
                raise ValueError("reference event lies outside its selected segment")
        return self


class FrozenManifest(FrozenDto):
    schema_version: Literal[1] = 1
    manifest_id: UUID
    frozen_at: datetime
    segments: tuple[FrozenSegment, ...]
    reference_state: Literal["ready", "pending_data"]
    missing_scenarios: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_reference_state(self) -> "FrozenManifest":
        if self.reference_state == "ready" and not self.segments:
            raise ValueError("a ready manifest must contain at least one segment")
        if self.reference_state == "ready" and self.missing_scenarios:
            raise ValueError("a ready manifest cannot have missing scenarios")
        return self
