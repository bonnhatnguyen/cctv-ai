from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.annotation_benchmark.contracts import (
    FrameSpan,
    FrozenManifest,
    SelectionItem,
)


def test_frame_span_rejects_reversed_or_negative_inclusive_bounds() -> None:
    with pytest.raises(ValidationError):
        FrameSpan(start_frame=-1, end_frame=4)
    with pytest.raises(ValidationError):
        FrameSpan(start_frame=5, end_frame=4)


def test_selection_rejects_unknown_fields_and_empty_scenario_tags() -> None:
    payload = {
        "segment_id": uuid4(),
        "clip_id": uuid4(),
        "span": {"start_frame": 2, "end_frame": 8},
        "partition": "exploratory",
        "parent_recording_id": None,
        "recording_days": [],
        "provenance_confirmed": False,
        "scenario_tags": [],
    }
    with pytest.raises(ValidationError):
        SelectionItem.model_validate(payload)
    payload["scenario_tags"] = ["entry"]
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        SelectionItem.model_validate(payload)


def test_frozen_manifest_cannot_claim_ready_without_segments() -> None:
    with pytest.raises(ValidationError):
        FrozenManifest(
            schema_version=1,
            manifest_id=uuid4(),
            frozen_at=datetime.now(timezone.utc),
            segments=[],
            reference_state="ready",
            missing_scenarios=[],
        )
