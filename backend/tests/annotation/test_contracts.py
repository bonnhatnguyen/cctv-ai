from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.annotation.contracts import (
    ActionAnnotationCreate,
    CameraSetupCreate,
    Point,
    RoiWrite,
)
from app.annotation.geometry import validate_polygon


def test_dtos_forbid_unknown_fields_and_non_strict_revisions():
    with pytest.raises(ValidationError):
        Point(x=0.5, y=0.5, z=1)
    with pytest.raises(ValidationError):
        RoiWrite(
            operation_id=uuid4(),
            expected_clip_revision=1.0,
            camera_setup_id=uuid4(),
            polygon=[Point(x=0, y=0), Point(x=1, y=0), Point(x=0, y=1)],
        )


def test_camera_setup_name_is_trimmed_and_must_not_be_blank():
    assert CameraSetupCreate(operation_id=uuid4(), name="  Quầy 1  ").name == "Quầy 1"
    with pytest.raises(ValidationError):
        CameraSetupCreate(operation_id=uuid4(), name="   ")


def test_rejects_crossed_polygon():
    with pytest.raises(ValueError, match="polygon"):
        validate_polygon(
            [
                Point(x=0, y=0),
                Point(x=1, y=1),
                Point(x=0, y=1),
                Point(x=1, y=0),
            ]
        )


@pytest.mark.parametrize(
    "points",
    [
        [(0, 0), (1, 0), (0, 0)],
        [(0, 0), (1, 0), (0, 1), (0, 0)],
        [(0, 0), (0.5, 0), (1, 0)],
        [(0, 0), (1, 0), (0, 1), (1, 0)],
    ],
)
def test_rejects_degenerate_or_touching_polygon(points):
    with pytest.raises(ValueError, match="polygon"):
        validate_polygon([Point(x=x, y=y) for x, y in points])


def test_accepts_polygon_on_normalized_boundary():
    points = [Point(x=0, y=0), Point(x=1, y=0), Point(x=1, y=1), Point(x=0, y=1)]
    assert validate_polygon(points) == points


def action_request(**overrides):
    values = {
        "operation_id": uuid4(),
        "expected_clip_revision": 4,
        "interaction_id": uuid4(),
        "label": "hand_in",
        "start_frame": 10,
        "end_frame": 20,
        "crossing_frame": 15,
        "object_kind": "unknown",
        "visibility": "clear",
        "uncertain_labels": [],
        "unclear_reason": None,
    }
    values.update(overrides)
    return ActionAnnotationCreate(**values)


@pytest.mark.parametrize("label", ["hand_in", "hand_out"])
def test_boundary_actions_require_crossing_inside_inclusive_interval(label):
    assert action_request(label=label, crossing_frame=10).crossing_frame == 10
    with pytest.raises(ValidationError, match="crossing_frame"):
        action_request(label=label, crossing_frame=None)
    with pytest.raises(ValidationError, match="crossing_frame"):
        action_request(label=label, crossing_frame=21)


@pytest.mark.parametrize("label", ["take_out", "put_in"])
def test_object_actions_forbid_crossing_and_require_interaction(label):
    assert action_request(label=label, crossing_frame=None).label == label
    with pytest.raises(ValidationError, match="crossing_frame"):
        action_request(label=label, crossing_frame=15)
    with pytest.raises(ValidationError, match="interaction"):
        action_request(label=label, crossing_frame=None, interaction_id=None)


def test_unclear_requires_reason_and_nonempty_unique_candidate_labels():
    request = action_request(
        label="unclear",
        interaction_id=None,
        crossing_frame=None,
        uncertain_labels=["hand_in", "take_out"],
        unclear_reason="occlusion",
    )
    assert request.uncertain_labels == ["hand_in", "take_out"]
    with pytest.raises(ValidationError, match="uncertain_labels"):
        action_request(
            label="unclear", interaction_id=None, crossing_frame=None,
            uncertain_labels=[], unclear_reason="occlusion"
        )
    with pytest.raises(ValidationError, match="unique"):
        action_request(
            label="unclear", interaction_id=None, crossing_frame=None,
            uncertain_labels=["hand_in", "hand_in"], unclear_reason="occlusion"
        )


def test_clear_action_forbids_unclear_metadata_and_invalid_interval():
    with pytest.raises(ValidationError, match="unclear"):
        action_request(uncertain_labels=["hand_out"], unclear_reason="boundary_ambiguous")
    with pytest.raises(ValidationError, match="end_frame"):
        action_request(start_frame=20, end_frame=10)
    with pytest.raises(ValidationError):
        action_request(extra_field=True)
