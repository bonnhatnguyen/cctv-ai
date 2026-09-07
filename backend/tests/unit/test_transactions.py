from app.rules.transactions import correlate
from app.schemas import EventKind, TransactionStatus, VideoEvent


def event(kind, at=1_000, confidence=.95):
    return VideoEvent(id=f"{kind}-{at}", kind=kind, confidence=confidence, start_ms=at, end_ms=at)


def test_goods_and_cash_received_close_a_visible_sale():
    tx = correlate([event(EventKind.GOODS_HANDOVER), event(EventKind.CASH_RECEIVED, 2_000)])[0]
    assert tx.status is TransactionStatus.CLOSED


def test_occlusion_yields_insufficient_observation_not_review():
    tx = correlate([event(EventKind.CASH_REMOVED_FROM_BASKET), event(EventKind.CASH_OCCLUDED, 2_000)])[0]
    assert tx.status is TransactionStatus.INSUFFICIENT_OBSERVATION


def test_unresolved_confirmed_cash_removal_requires_review():
    tx = correlate([event(EventKind.CASH_REMOVED_FROM_BASKET), event(EventKind.CAMERA_DEGRADED, 31_000, 0)])[0]
    assert tx.status is TransactionStatus.INSUFFICIENT_OBSERVATION
