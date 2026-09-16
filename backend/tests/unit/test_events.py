from app.rules.events import derive_events
from app.schemas import Observation, RuleConfig


def observation(timestamp, kind="cash_note", roi=None, track=None):
    return Observation(camera_id="cam-a", timestamp_ms=timestamp, kind=kind, confidence=.9,
                       roi_id=roi, track_id=track, bbox=(.1, .1, .2, .2))


def test_cash_leaving_basket_with_same_hand_emits_removal():
    events = derive_events([
        observation(1_000, roi="cash_basket", track="cash-1"),
        observation(2_000, kind="hand", track="hand-1"), observation(2_000, roi="seller_area", track="cash-1"),
    ], RuleConfig())
    assert events[-1].kind == "cash_removed_from_basket"


def test_single_occlusion_emits_occluded_not_lost_or_review():
    events = derive_events([
        observation(1_000, roi="seller_area", track="cash-1"),
        observation(2_000, kind="hand", track="hand-1"),
    ], RuleConfig())
    assert [event.kind for event in events] == ["cash_occluded"]
