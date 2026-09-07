"""Minimal review API. It exposes no RTSP credentials or accusatory language."""

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import create_all, session_factory
from app.schemas import ReviewOutcome, TransactionStatus
from app.services.cases import append_decision, list_review_cases
from app.models import ReviewCase
from app.video.live import LiveStreamManager

live_manager = LiveStreamManager("./data/live", {"cam-a": "TRANSACTION_RTSP_CAM_A", "cam-b": "TRANSACTION_RTSP_CAM_B"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all(get_settings().database_url)
    live_manager.start()
    yield
    live_manager.stop()


app = FastAPI(title="Transaction evidence review", lifespan=lifespan)
app.mount("/live", StaticFiles(directory="./data/live", check_dir=False), name="live")


def get_session():
    factory = session_factory(get_settings().database_url)
    with factory() as session:
        yield session


class DecisionInput(BaseModel):
    outcome: ReviewOutcome
    note: str | None = Field(default=None, max_length=2_000)


@app.get("/api/review-cases")
def review_cases(status: TransactionStatus = TransactionStatus.REVIEW_REQUIRED, session: Session = Depends(get_session)):
    return [{"id": case.id, "status": case.status, "reason": case.reason, "clips": case.clips,
             "event_timeline": case.event_timeline} for case in list_review_cases(session, status)]


@app.post("/api/review-cases/{case_id}/decision")
def review_decision(case_id: str, payload: DecisionInput, session: Session = Depends(get_session)):
    case = session.get(ReviewCase, case_id)
    if not case:
        raise HTTPException(404, "review case not found")
    decision = append_decision(session, case, payload.outcome, payload.note)
    return {"id": decision.id, "outcome": decision.outcome, "note": decision.note}


@app.get("/api/live/status")
def live_status():
    return live_manager.statuses()
