from __future__ import annotations

import numpy as np
import pytest


class ArrayBox:
    def __init__(self, xyxy, cls, conf, ids=None):
        self.xyxy = np.asarray(xyxy, dtype=np.float32)
        self.cls = np.asarray(cls, dtype=np.float32)
        self.conf = np.asarray(conf, dtype=np.float32)
        self.id = None if ids is None else np.asarray(ids, dtype=np.float32)

    def __len__(self):
        return len(self.cls)


class FakeResult:
    names = {0: "person", 1: "bicycle"}
    speed = {"inference": 4.5}

    def __init__(self, boxes):
        self.boxes = boxes


class FakeModel:
    def __init__(self, result_factory):
        self.result_factory = result_factory
        self.calls = []
        self.device = "cuda:0"

    def track(self, frame, **kwargs):
        self.calls.append((frame, kwargs))
        return [self.result_factory()]


@pytest.fixture
def frame():
    return np.zeros((360, 640, 3), dtype=np.uint8)


@pytest.fixture
def model_factory():
    def factory(_path):
        return FakeModel(lambda: FakeResult(ArrayBox([[10, 20, 100, 200]], [0], [0.87], [7])))

    return factory


@pytest.fixture
def person_tracker(model_factory):
    from app.v1.tracker import PersonTracker

    return PersonTracker("model.pt", "cpu", model_factory=model_factory)


@pytest.fixture
def tracker_without_ids(frame):
    from app.v1.tracker import PersonTracker

    def factory(_path):
        return FakeModel(lambda: FakeResult(ArrayBox([[10, 20, 100, 200]], [0], [0.87], None)))

    return PersonTracker("model.pt", "cpu", model_factory=factory)
