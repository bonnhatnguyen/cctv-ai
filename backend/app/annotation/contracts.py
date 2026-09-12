from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


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


class AnnotationConflictDetail(Dto):
    code: Literal["annotation_conflict"]
    conflicting_annotation_id: UUID


class ErrorView(Dto):
    detail: str | AnnotationConflictDetail


class ClipListView(Dto):
    items: list[ClipView]
    next_cursor: str | None


class CameraSetupListView(Dto):
    items: list[CameraSetupView]
    next_cursor: str | None


ActionLabel = Literal["hand_in", "hand_out", "take_out", "put_in", "unclear"]
ClearActionLabel = Literal["hand_in", "hand_out", "take_out", "put_in"]
HandSide = Literal["left", "right", "unknown"]
ObjectKind = Literal["cash", "other", "unknown"]
Visibility = Literal["clear", "occluded"]
ReviewState = Literal["draft", "confirmed", "needs_review"]
UnclearReason = Literal[
    "occlusion",
    "boundary_ambiguous",
    "clip_boundary",
    "actor_ambiguous",
    "object_ambiguous",
]


class InteractionCreate(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)
    hand: HandSide
    tracking_job_id: UUID | None = None
    local_track_id: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_tracking_reference(self):
        if (self.tracking_job_id is None) != (self.local_track_id is None):
            raise ValueError("tracking_job_id and local_track_id must be supplied together")
        return self


class InteractionUpdate(InteractionCreate):
    expected_interaction_revision: StrictInt = Field(ge=1)


class InteractionView(Dto):
    id: UUID
    clip_id: UUID
    revision: StrictInt = Field(ge=1)
    hand: HandSide
    tracking_job_id: UUID | None
    local_track_id: StrictInt | None


class ActionFields(Dto):
    interaction_id: UUID | None
    label: ActionLabel
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)
    crossing_frame: StrictInt | None = Field(default=None, ge=0)
    object_kind: ObjectKind
    visibility: Visibility
    uncertain_labels: list[ClearActionLabel] = Field(default_factory=list, max_length=4)
    unclear_reason: UnclearReason | None = None

    @model_validator(mode="after")
    def validate_action_semantics(self):
        if self.end_frame < self.start_frame:
            raise ValueError("end_frame must be greater than or equal to start_frame")
        if self.label in {"hand_in", "hand_out"}:
            if self.crossing_frame is None:
                raise ValueError("crossing_frame is required for hand boundary actions")
            if not self.start_frame <= self.crossing_frame <= self.end_frame:
                raise ValueError("crossing_frame must be inside the inclusive interval")
        elif self.crossing_frame is not None:
            raise ValueError("crossing_frame is only valid for hand_in and hand_out")

        if self.label == "unclear":
            if not self.uncertain_labels:
                raise ValueError("uncertain_labels must be non-empty for unclear")
            if len(set(self.uncertain_labels)) != len(self.uncertain_labels):
                raise ValueError("uncertain_labels must be unique")
            if self.unclear_reason is None:
                raise ValueError("unclear_reason is required for unclear")
        else:
            if self.interaction_id is None:
                raise ValueError("interaction_id is required for a clear action")
            if self.uncertain_labels or self.unclear_reason is not None:
                raise ValueError("unclear metadata is only valid for unclear")
        return self


class ActionAnnotationCreate(ActionFields):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)


class ActionAnnotationUpdate(ActionAnnotationCreate):
    expected_annotation_revision: StrictInt = Field(ge=1)


class ActionMutation(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)
    expected_annotation_revision: StrictInt = Field(ge=1)


class ActionAnnotationView(ActionFields):
    id: UUID
    clip_id: UUID
    roi_revision_id: UUID
    revision: StrictInt = Field(ge=1)
    review_state: ReviewState
    guideline_version: Literal[1] = 1
    deleted: bool


class ReviewCoverageWrite(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)
    reviewed_labels: list[ClearActionLabel] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_coverage(self):
        if self.end_frame < self.start_frame:
            raise ValueError("end_frame must be greater than or equal to start_frame")
        if len(set(self.reviewed_labels)) != len(self.reviewed_labels):
            raise ValueError("reviewed_labels must be unique")
        return self


class ReviewCoverageView(Dto):
    id: UUID
    clip_id: UUID
    roi_revision_id: UUID
    revision: StrictInt = Field(ge=1)
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)
    reviewed_labels: list[ClearActionLabel]
    guideline_version: Literal[1] = 1
    active: bool


class ActionWorkspaceView(Dto):
    clip_id: UUID
    clip_revision: StrictInt = Field(ge=0)
    roi_revision_id: UUID
    interactions: list[InteractionView]
    annotations: list[ActionAnnotationView]
    review_coverage: list[ReviewCoverageView]


AssistanceModelId = Literal["dino", "mediapipe"]
AssistanceRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
SuggestionReviewState = Literal["pending", "accepted", "rejected", "stale"]
SuggestionReason = Literal[
    "crossing", "boundary", "track_gap", "association", "clip_boundary"
]


class AssistanceRunCreate(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)
    model: AssistanceModelId
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_frame < self.start_frame:
            raise ValueError("end_frame must be greater than or equal to start_frame")
        return self


class AssistanceRunView(Dto):
    id: UUID
    clip_id: UUID
    model: AssistanceModelId
    status: AssistanceRunStatus
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)
    processed_frames: StrictInt = Field(ge=0)
    scheduled_frames: StrictInt = Field(ge=0)
    error_code: str | None
    source_sha256: str
    roi_revision_id: UUID
    guideline_version: Literal[1] = 1
    device: Literal["cpu", "cuda:0"]
    config_sha256: str | None
    asset_sha256: str | None
    created_at: datetime
    updated_at: datetime


class AssistanceRunListView(Dto):
    items: list[AssistanceRunView]
    next_cursor: str | None


class AssistanceSuggestionView(Dto):
    id: UUID
    run_id: UUID
    clip_id: UUID
    proposal_key: str
    label: Literal["hand_in", "hand_out"] | None
    action_start_frame: StrictInt | None = Field(default=None, ge=0)
    action_end_frame: StrictInt | None = Field(default=None, ge=0)
    view_start_frame: StrictInt = Field(ge=0)
    view_end_frame: StrictInt = Field(ge=0)
    crossing_estimate: StrictInt | None = Field(default=None, ge=0)
    crossing_bracket_start: StrictInt | None = Field(default=None, ge=0)
    crossing_bracket_end: StrictInt | None = Field(default=None, ge=0)
    reason: SuggestionReason
    review_state: SuggestionReviewState
    accepted_annotation_id: UUID | None


class AssistanceSuggestionListView(Dto):
    items: list[AssistanceSuggestionView]
    next_cursor: str | None


class AssistanceModelInfo(Dto):
    model: AssistanceModelId
    available: bool
    device: Literal["cpu", "cuda:0"]
    error_code: str | None


class AssistanceRunCancel(Dto):
    operation_id: UUID


class SuggestionReject(Dto):
    operation_id: UUID
    expected_clip_revision: StrictInt = Field(ge=0)
