from app.video.rtsp import Frame
from app.vision.detector import Detector, RawDetection


class Model:
    def __init__(self, labels): self.labels = labels
    def predict(self, image):
        return [RawDetection(label, confidence, (0.1, 0.1, 0.2, 0.2)) for label, confidence in self.labels]


def detector_from_labels(labels):
    return Detector(Model(labels))


def frame_at(timestamp_ms): return Frame("cam-a", timestamp_ms, "frame")


def test_detector_discards_unknown_model_labels():
    result = detector_from_labels([("wallet", .99), ("cash_note", .90)]).detect(frame_at(1_000))
    assert [o.kind for o in result] == ["cash_note"]
