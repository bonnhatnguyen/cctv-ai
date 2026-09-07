from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import app, get_session
from app.db import create_all
from app.models import TransactionRecord
from app.services.cases import create_review_case


def test_review_case_contains_neutral_reason_and_clips(tmp_path):
    url = f"sqlite:///{tmp_path / 'api.db'}"
    create_all(url)
    factory = sessionmaker(bind=create_engine(url))
    with factory() as session:
        session.add(TransactionRecord(id="tx-1", status="review_required", opened_at_ms=1, updated_at_ms=1, reason=None, event_ids=[]))
        session.commit()
        create_review_case(session, "tx-1", {"cam-a": "/clips/a.mp4", "cam-b": "/clips/b.mp4"}, [])
    def override_session():
        with factory() as session:
            yield session
    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/review-cases?status=review_required")
        payload = response.json()[0]
        assert payload["reason"] == "cash_removal_has_no_observed_destination"
        assert set(payload["clips"]) == {"cam-a", "cam-b"}
    finally:
        app.dependency_overrides.clear()
