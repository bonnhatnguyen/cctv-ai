# Annotation contract

Annotate only video evidence. Never annotate identity, faces, alleged intent, prices, or POS data.

Object classes are `person`, `hand`, `goods`, `cash_note`, `cash_stack`, and `cash_basket`. Link object tracks only inside one camera. Record hand-object contact intervals and the named ROI.

Event labels are `goods_handover`, `cash_received`, `cash_removed_from_basket`, `cash_returned_to_basket`, `cash_handed_to_customer`, `cash_occluded`, `cash_lost`, and `camera_degraded`. Split train, validation, and test sets by recording day; a day may appear in exactly one split.

Use `insufficient_observation` for an occlusion, missing track, unclear role, camera failure, or cross-camera disagreement. Use `review_required` only when a strong, confirmed cash removal has no valid observed destination after the configured window. Review outcomes are `confirmed`, `false_positive`, and `insufficient_data`.
