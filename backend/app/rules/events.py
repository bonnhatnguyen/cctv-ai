"""Convert per-camera observations into conservative atomic video events."""

from collections import defaultdict
from typing import Sequence
from uuid import uuid4

from app.schemas import EventKind, Observation, RuleConfig, VideoEvent


def derive_events(observations: Sequence[Observation], config: RuleConfig) -> list[VideoEvent]:
    """Infer only explicit transitions; ambiguous or brief changes remain unlabelled."""
    frames: dict[int, list[Observation]] = defaultdict(list)
    for observation in observations:
        frames[observation.timestamp_ms].append(observation)
    events: list[VideoEvent] = []
    prior_cash_in_basket: Observation | None = None
    prior_cash: Observation | None = None
    occlusion_started: int | None = None
    for timestamp in sorted(frames):
        current = frames[timestamp]
        cash = [item for item in current if item.kind in {"cash_note", "cash_stack"}]
        hands = [item for item in current if item.kind == "hand"]
        basket_cash = next((item for item in cash if item.roi_id == "cash_basket"), None)
        outside_cash = next((item for item in cash if item.roi_id != "cash_basket"), None)
        if prior_cash_in_basket and outside_cash and hands:
            events.append(_event(EventKind.CASH_REMOVED_FROM_BASKET, timestamp, [prior_cash_in_basket, outside_cash, hands[0]]))
            prior_cash_in_basket = None
        elif outside_cash and basket_cash and prior_cash and prior_cash.roi_id != "cash_basket":
            events.append(_event(EventKind.CASH_RETURNED_TO_BASKET, timestamp, [prior_cash, basket_cash]))
        if prior_cash and not cash and hands:
            if occlusion_started is None:
                occlusion_started = timestamp
                events.append(_event(EventKind.CASH_OCCLUDED, timestamp, [prior_cash, hands[0]]))
        elif cash:
            occlusion_started = None
        if occlusion_started is not None and timestamp - occlusion_started >= config.destination_window_ms:
            events.append(_event(EventKind.CASH_LOST, timestamp, [prior_cash] if prior_cash else []))
            occlusion_started = None
        if basket_cash:
            prior_cash_in_basket = basket_cash
        if cash:
            prior_cash = cash[0]
    return events


def _event(kind: EventKind, timestamp: int, sources: list[Observation]) -> VideoEvent:
    return VideoEvent(id=str(uuid4()), kind=kind, confidence=min(item.confidence for item in sources) if sources else 0,
        start_ms=timestamp, end_ms=timestamp, camera_id=sources[0].camera_id if sources else None,
        source_observation_ids=tuple(item.track_id or "" for item in sources),
        related_track_ids=tuple(item.track_id for item in sources if item.track_id))
