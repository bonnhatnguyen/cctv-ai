from app.vision.runtime import PersonInferenceWorker


def test_runtime_status_starts_with_no_transaction_evidence():
    worker = PersonInferenceWorker("cam-a", "MISSING_RTSP")
    assert worker.snapshot()["event_kinds"] == []
    assert worker.snapshot()["transaction_statuses"] == []
