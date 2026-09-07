"""Conservative transaction correlation: only confirmed unresolved removals request review."""

from typing import Sequence
from uuid import uuid4

from app.schemas import EventKind, RuleConfig, Transaction, TransactionStatus, VideoEvent


def correlate(events: Sequence[VideoEvent], config: RuleConfig | None = None) -> list[Transaction]:
    config = config or RuleConfig()
    if not events:
        return []
    groups: list[list[VideoEvent]] = []
    for event in sorted(events, key=lambda item: item.start_ms):
        if not groups or event.start_ms - groups[-1][-1].end_ms > config.correlation_window_ms:
            groups.append([event])
        else:
            groups[-1].append(event)
    return [_correlate_group(group, config) for group in groups]


def _correlate_group(events: list[VideoEvent], config: RuleConfig) -> Transaction:
    tx = Transaction(id=str(uuid4()), opened_at_ms=events[0].start_ms, updated_at_ms=events[-1].end_ms,
                     status=TransactionStatus.OPEN, event_ids=())
    for event in events:
        tx = advance(tx, event, config)
    return tx


def advance(transaction: Transaction, event: VideoEvent, config: RuleConfig) -> Transaction:
    ids = transaction.event_ids + ((event.id or f"{event.kind}:{event.start_ms}"),)
    status, reason = transaction.status, transaction.reason
    if event.kind in {EventKind.CASH_OCCLUDED, EventKind.CASH_LOST, EventKind.CAMERA_DEGRADED}:
        status, reason = TransactionStatus.INSUFFICIENT_OBSERVATION, "observation_interrupted"
    elif event.kind == EventKind.CASH_REMOVED_FROM_BASKET:
        status, reason = TransactionStatus.OBSERVING, None
    elif event.kind in {EventKind.GOODS_HANDOVER, EventKind.CASH_RECEIVED, EventKind.CASH_HANDED_TO_CUSTOMER, EventKind.CASH_RETURNED_TO_BASKET}:
        if status == TransactionStatus.OBSERVING or event.kind == EventKind.CASH_RECEIVED:
            status, reason = TransactionStatus.CLOSED, "observed_valid_destination"
        else:
            status = TransactionStatus.PARTIALLY_MATCHED
    return transaction.model_copy(update={"status": status, "reason": reason, "updated_at_ms": event.end_ms, "event_ids": ids})


def expire(transaction: Transaction, now_ms: int, config: RuleConfig) -> Transaction:
    """Advance a still-observed, high-evidence removal only after its destination window."""
    if transaction.status is TransactionStatus.OBSERVING and now_ms - transaction.opened_at_ms >= config.destination_window_ms:
        return transaction.model_copy(update={"status": TransactionStatus.REVIEW_REQUIRED,
            "reason": "cash_removal_has_no_observed_destination", "updated_at_ms": now_ms})
    return transaction
