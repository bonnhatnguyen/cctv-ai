from app.schemas import Observation
from app.vision.tracker import Tracker


def cash(x, y):
    return Observation(camera_id="cam-a", timestamp_ms=1_000, kind="cash_note", confidence=.9,
                       bbox=(x / 100, y / 100, (x + 10) / 100, (y + 10) / 100))


def test_tracker_keeps_track_id_for_same_camera_motion():
    tracker = Tracker()
    first, second = tracker.update("cam-a", [cash(10, 10)]), tracker.update("cam-a", [cash(12, 11)])
    assert first[0].track_id == second[0].track_id
