from __future__ import annotations

import pytest

from .conftest import ArrayBox, FakeResult


def test_missing_ids_do_not_create_fake_tracks(tracker_without_ids, frame):
    assert tracker_without_ids.track_frame(frame).people == []


def test_coordinates_are_original_pixels(person_tracker, frame):
    person = person_tracker.track_frame(frame).people[0]
    assert person.track_id == 7
    assert person.xyxy == (10.0, 20.0, 100.0, 200.0)
    assert person.confidence == pytest.approx(0.87)


def test_tracker_filters_non_person_classes(person_tracker, frame, model_factory):
    from app.v1.tracker import PersonTracker

    # Keep a real-shaped result and replace its class IDs after construction.
    model = model_factory("x")
    model.result_factory = lambda: FakeResult(ArrayBox(
        [[10, 20, 100, 200], [1, 1, 50, 50]], [0, 1], [0.8, 0.99], [3, 99]
    ))
    tracker = PersonTracker("model.pt", "cpu", model_factory=lambda _path: model)
    people = tracker.track_frame(frame).people
    assert [(p.track_id, p.xyxy) for p in people] == [(3, (10.0, 20.0, 100.0, 200.0))]


def test_tracker_state_is_not_shared_between_instances(frame):
    from app.v1.tracker import PersonTracker

    models = []

    def factory(_path):
        model = type("Model", (), {})()
        model.device = "cpu"
        model.track = lambda _frame, **_kwargs: []
        models.append(model)
        return model

    first = PersonTracker("model.pt", "cpu", model_factory=factory)
    second = PersonTracker("model.pt", "cpu", model_factory=factory)
    first.track_frame(frame)
    second.track_frame(frame)
    assert len(models) == 2
