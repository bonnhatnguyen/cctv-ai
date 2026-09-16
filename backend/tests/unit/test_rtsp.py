from app.schemas import CameraHealth
from app.video.rtsp import RtspFrameSource


class FailingReader:
    def __init__(self, failures): self.failures = failures
    def read(self):
        if self.failures:
            self.failures -= 1
            return None
        return "image"


def test_stream_marks_health_degraded_after_three_read_failures():
    source = RtspFrameSource("cam-a", reader=FailingReader(3))
    assert source.next_frame() is None
    assert source.next_frame() is None
    assert source.next_frame() is None
    assert source.health.status == CameraHealth.DEGRADED
