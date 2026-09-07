import pytest
from pydantic import ValidationError

from app.schemas import Observation, TransactionStatus


def test_review_status_is_neutral_and_complete():
    assert TransactionStatus.REVIEW_REQUIRED.value == "review_required"
    assert "cheat" not in {status.value for status in TransactionStatus}


def test_observation_requires_utc_milliseconds():
    with pytest.raises(ValidationError):
        Observation(camera_id="cam-a", timestamp_ms=-1, kind="cash_note", confidence=0.9)


def test_observation_rejects_non_normalized_bbox():
    with pytest.raises(ValidationError):
        Observation(camera_id="cam-a", timestamp_ms=0, kind="cash_note", confidence=0.9, bbox=(0, 0, 1.2, 1))
