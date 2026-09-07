"""Review-case persistence; decisions append audit records without mutating evidence."""

from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ReviewCase, ReviewDecision
from app.schemas import ReviewOutcome, TransactionStatus


def create_review_case(session: Session, transaction_id: str, clips: dict[str, str | None], event_timeline: list[dict],
                       reason: str = "cash_removal_has_no_observed_destination") -> ReviewCase:
    case = ReviewCase(id=str(uuid4()), transaction_id=transaction_id, status=TransactionStatus.REVIEW_REQUIRED.value,
        reason=reason, clips=clips, event_timeline=event_timeline)
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def list_review_cases(session: Session, status: TransactionStatus = TransactionStatus.REVIEW_REQUIRED) -> list[ReviewCase]:
    return list(session.scalars(select(ReviewCase).where(ReviewCase.status == status.value).order_by(ReviewCase.id)))


def append_decision(session: Session, case: ReviewCase, outcome: ReviewOutcome, note: str | None) -> ReviewDecision:
    decision = ReviewDecision(id=str(uuid4()), transaction_id=case.transaction_id, outcome=outcome.value, note=note)
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return decision
