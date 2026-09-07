from app.video.rtsp import Frame, RollingClipStore


def frame_at(timestamp_ms):
    return Frame("cam-a", timestamp_ms, "synthetic")


def test_clip_export_covers_requested_window(tmp_path):
    store = RollingClipStore(tmp_path, retention_seconds=30)
    store.append("cam-a", frame_at(1_000)); store.append("cam-a", frame_at(2_000))
    assert store.export("cam-a", 1_000, 2_000).exists()
