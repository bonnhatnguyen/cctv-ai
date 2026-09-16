from app.vision.hands import HandLandmarkTracker


def test_hand_tracker_keeps_id_for_nearby_hand() -> None:
    tracker = HandLandmarkTracker.__new__(HandLandmarkTracker)
    tracker.camera_id, tracker._tracks, tracker._next_id = "cam-a", {}, 1
    first = tracker._associate([((0.1, 0.1, 0.2, 0.2), 0.9)])
    second = tracker._associate([((0.12, 0.1, 0.22, 0.2), 0.9)])
    assert first[0].track_id == second[0].track_id == "cam-a-hand-1"


def test_hand_tracker_uses_new_id_for_distant_hand() -> None:
    tracker = HandLandmarkTracker.__new__(HandLandmarkTracker)
    tracker.camera_id, tracker._tracks, tracker._next_id = "cam-a", {}, 1
    first = tracker._associate([((0.1, 0.1, 0.2, 0.2), 0.9)])
    second = tracker._associate([((0.7, 0.7, 0.8, 0.8), 0.9)])
    assert first[0].track_id != second[0].track_id
