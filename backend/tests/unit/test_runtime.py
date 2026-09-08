from app.schemas import Observation
from app.vision.runtime import PersonInferenceWorker, _suppress_overlaps


def test_runtime_status_starts_with_no_transaction_evidence():
    worker = PersonInferenceWorker("cam-a", "MISSING_RTSP")
    assert worker.snapshot()["event_kinds"] == []
    assert worker.snapshot()["transaction_statuses"] == []


def test_overlap_filter_keeps_only_the_strongest_detection():
    strong = Observation(camera_id="cam-a", timestamp_ms=1, kind="person", confidence=.9, bbox=(.1, .1, .5, .5))
    weak = Observation(camera_id="cam-a", timestamp_ms=1, kind="person", confidence=.5, bbox=(.12, .12, .48, .48))
    assert _suppress_overlaps([weak, strong]) == [strong]
