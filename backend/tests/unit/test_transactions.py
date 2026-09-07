from app.rules.transactions import correlate, expire
from app.schemas import EventKind, RuleConfig, TransactionStatus, VideoEvent


def event(kind, at=1_000, confidence=.95):
    return VideoEvent(id=f"{kind}-{at}", kind=kind, confidence=confidence, start_ms=at, end_ms=at)


def test_goods_and_cash_received_close_a_visible_sale():
    tx = correlate([event(EventKind.GOODS_HANDOVER), event(EventKind.CASH_RECEIVED, 2_000)])[0]
    assert tx.status is TransactionStatus.CLOSED


def test_occlusion_yields_insufficient_observation_not_review():
    tx = correlate([event(EventKind.CASH_REMOVED_FROM_BASKET), event(EventKind.CASH_OCCLUDED, 2_000)])[0]
    assert tx.status is TransactionStatus.INSUFFICIENT_OBSERVATION


def test_unresolved_confirmed_cash_removal_requires_review():
    tx = correlate([event(EventKind.CASH_REMOVED_FROM_BASKET)])[0]
    assert expire(tx, 31_000, RuleConfig()).status is TransactionStatus.REVIEW_REQUIRED
