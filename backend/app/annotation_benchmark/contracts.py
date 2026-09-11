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


class RunConfig(FrozenDto):
    schema_version: Literal[1] = 1
    stride: StrictInt = Field(default=1, ge=1)
    margin_ratio: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    context_ms: StrictInt = Field(default=1000, ge=0)
    max_input_side: StrictInt = Field(default=960, ge=32)
    association_distance: float = Field(default=0.15, gt=0, le=1, allow_inf_nan=False)
    ambiguity_margin: float = Field(default=0.03, ge=0, le=1, allow_inf_nan=False)
    boundary_epsilon: float = Field(default=0.002, ge=0, le=1, allow_inf_nan=False)
    min_side_samples: StrictInt = Field(default=2, ge=2)
    motion_epsilon: float = Field(default=0.002, ge=0, le=1, allow_inf_nan=False)
    dino_box_threshold: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    dino_text_threshold: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    mp_detection_threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    mp_presence_threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    mp_tracking_threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    max_hands: StrictInt = Field(default=4, ge=1, le=16)
    device: Literal["cpu", "cuda:0"] = "cpu"
    segment_deadline_seconds: StrictInt = Field(default=600, ge=1)
    decoder_stall_seconds: StrictInt = Field(default=30, ge=1)
    output_quota_bytes: StrictInt = Field(default=512 * 1024**2, ge=1)
    free_disk_reserve_bytes: StrictInt = Field(default=2 * 1024**3, ge=0)
