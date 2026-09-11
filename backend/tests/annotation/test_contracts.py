from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.annotation.contracts import CameraSetupCreate, Point, RoiWrite
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
