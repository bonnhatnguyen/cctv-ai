from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from app.annotation_benchmark.contracts import Proposal

from .contracts import AssistanceModelId, Dto


class AssistanceChildRequest(Dto):
    schema_version: Literal[1] = 1
    run_id: UUID
    clip_id: UUID
    source_job_id: UUID
    source_path: Path
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    roi_revision_id: UUID
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=64)
    frame_count: StrictInt = Field(gt=0)
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    fps_num: StrictInt = Field(gt=0)
    fps_den: StrictInt = Field(gt=0)
    sar_num: StrictInt = Field(gt=0)
    sar_den: StrictInt = Field(gt=0)
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(ge=0)
    model: AssistanceModelId
    model_root: Path
    device: Literal["cpu", "cuda:0"]
    stride: StrictInt = Field(default=5, ge=1)

    @field_validator("source_path", "model_root", mode="before")
    @classmethod
    def reject_non_local_path(cls, value):
        text = str(value)
        parsed = urlparse(text)
        if text.startswith("\\\\") or (
            parsed.scheme and not (len(parsed.scheme) == 1 and text[1:3] in {":\\", ":/"})
        ):
            raise ValueError("only local non-UNC paths are accepted")
        path = Path(text)
        if not path.is_absolute():
            raise ValueError("path must be absolute")
        return text

    @field_validator("source_path", "model_root")
    @classmethod
    def resolve_local_path(cls, value: Path) -> Path:
        return value.resolve()

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_frame < self.start_frame or self.end_frame >= self.frame_count:
            raise ValueError("frame interval is outside the source")
        return self


class AssistanceChildResult(Dto):
    schema_version: Literal[1] = 1
    run_id: UUID
    scheduled_frames: list[StrictInt]
    observed_frames: list[StrictInt]
    proposals: list[Proposal]

    @model_validator(mode="after")
    def require_complete_schedule(self):
        if self.observed_frames != self.scheduled_frames:
            raise ValueError("observed frames must equal the scheduled frames")
        return self
