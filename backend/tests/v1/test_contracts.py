from app.v1.contracts import JobStatus, Progress, RunOptions, Stage


def test_contracts_use_explicit_v1_statuses():
    assert JobStatus.READY.value == "ready"
    assert Stage.TRACKING.value == "tracking"
    assert Progress(Stage.TRACKING, 4, 10).processed_frames == 4
    assert RunOptions("yolo26n.pt").image_size == 960


def test_v1_settings_have_independent_defaults():
    from app.v1.settings import V1Settings

    settings = V1Settings()
    assert settings.device == "auto"
    assert settings.image_size == 960
    assert settings.max_upload_bytes == 4 * 1024**3
