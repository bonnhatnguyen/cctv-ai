# One-Camera Observation Redesign

## Goal

Replace the current generic prompt-based detection pipeline with public,
purpose-specific model adapters that show tracked people, whole-body/hand pose,
and observable hand-motion sequences on one RTSP camera. A Vietnamese-banknote
layer may enrich a sequence only when a legally usable local checkpoint is
available. The system is an observation tool only: it must not create a
transaction, shortage, theft, or review decision from this one camera.

## Constraints

- One RTSP camera is configured. There is no second evidence camera.
- No site-specific labelled clips are available for training or fine-tuning.
- RTSP credentials remain in environment variables and never appear in API
  responses, logs, UI, source control, or test fixtures.
- Use existing public models or repositories only. Do not substitute a generic
  prompt detector when the Vietnamese-banknote model is missing or fails.
- Use neutral Vietnamese UI language: `quan sát được`, `không đủ quan sát`,
  and `model chưa sẵn sàng`. Do not label a person or event as fraudulent.
- A detector failure leaves its layer unavailable while other layers continue.

## Chosen Sources

| Concern | Source | Required use |
| --- | --- | --- |
| People and multi-object ID | Ultralytics YOLO tracking with ByteTrack | Run a COCO person model with `model.track(..., persist=True, tracker=...)`; expose its returned ID only after basic duplicate suppression. |
| Whole-body and hands | OpenMMLab MMPose RTMPose whole-body model | Run pose inference per tracked person; retain wrist, elbow, hip, and hand keypoints with visibility scores. |
| Action framework | OpenMMLab MMAction2 design and temporal-window conventions | Use its public, tested video-understanding structure as the adapter boundary, but implement only transparent movement rules because its public checkpoints are not trained for Vietnamese cashier actions. |
| Vietnamese banknotes | Vietnamese Banknote YOLO11 model by bao-vu on Roboflow Universe | Treat this as an optional enrichment layer. It is unavailable until a local, licensed checkpoint and documented class map are obtained; do not call a hosted API or send frames externally without a later explicit approval. |

The current YOLO-World prompt path for `banknote`, `cash basket`, `goods`, and
`human hand` is removed from the live runtime. A model trained for other
countries' banknotes is not an eligible fallback.

## Architecture

```text
RTSP frame (cam-a)
    |
    +-- Person adapter: Ultralytics COCO person model + ByteTrack
    |       -> Observation(kind="person", track_id="cam-a-person-<id>")
    |
    +-- Pose adapter: MMPose RTMPose whole-body model, per tracked person
    |       -> wrist / elbow / hip / hand keypoints with visibility scores
    |
    +-- Optional VND banknote adapter: licensed local checkpoint only
            -> Observation(kind="vnd_note", denomination=<published class>)
             -> ByteTrack ID scoped to cam-a
    |
    v
Temporal action observer (16-frame sliding window per person)
    -> `tay_huong_khach`
    -> `tay_vao_vung_tui`
    -> `tien_di_cung_tay` only with a visible VND-note track
    -> `tien_mat_dau_gan_vung_tui` only with a previously visible VND-note track
    -> `khong_du_quan_sat` for occlusion, a missing pose, or missing note layer
    |
    v
Observation aggregator
    -> delayed overlay aligned with HLS video
    -> model/layer health and per-class counts
    -> append-only diagnostic observations only
```

Each adapter owns model loading, input preprocessing, normalized output boxes or
keypoints, confidence thresholds, and a neutral availability state. The temporal
observer is the only component allowed to emit an action observation. It must
require at least eight consecutive valid frames inside a sixteen-frame window;
it never infers an action from one frame. The aggregator is the only component
allowed to combine outputs for the frontend. It may never turn observations into
`cash_removed_from_basket`, `review_required`, or a transaction status while
only `cam-a` is active.

## Model Loading and Health

On startup, the application validates each configured artefact before opening
the RTSP stream.

- `person`: ready only if the official Ultralytics person model is available.
- `pose`: ready only if the MMPose RTMPose whole-body checkpoint is available.
- `vnd_note`: `model_unavailable` until a local Vietnamese-banknote model and
  a checked-in, documented class map are available. The remaining layers still
  run normally.

The live status endpoint returns one state per layer: `ready`, `running`,
`model_unavailable`, or `inference_failed`. The UI shows unavailable layers in
plain language and renders no fabricated boxes. The API must not surface model
paths, RTSP URLs, tokens, or exception text.

## Tracking, Action, and Overlay Rules

- Apply class-aware non-maximum suppression before any tracker update.
- Person and VND-note tracks use independent ByteTrack instances. IDs are
  unique within a camera and class namespace.
- Pose keypoints are associated to the person track whose box contains the
  pose centre. A keypoint below confidence 0.55 is treated as absent.
- A `tay_huong_khach` observation requires two distinct person tracks, one
  tracked wrist moving toward the other person's box for eight valid frames.
- A `tay_vao_vung_tui` observation requires a wrist approaching that person's
  hip region for eight valid frames. The UI calls this `tay vào vùng túi`, not
  `đút tiền vào túi`.
- A `tien_di_cung_tay` observation requires the centre of a visible VND note
  to remain within the wrist neighbourhood for eight valid frames.
- A `tien_mat_dau_gan_vung_tui` observation requires `tien_di_cung_tay`, then
  a lost VND-note track while the wrist remains in the hip region. Its UI label
  is `tiền không còn quan sát gần vùng túi`, never `đút túi`.
- If a person, wrist, or VND note is hidden, the temporal observer emits
  `khong_du_quan_sat` and clears the partial sequence.
- The overlay reads the detection history nearest to the active HLS delay.
- Labels are exactly `khách`, `tay hướng khách`, `tay vào vùng túi`, `tiền đi
  cùng tay`, `tiền không còn quan sát gần vùng túi`, `không đủ quan sát`, or
  `tiền <mệnh giá>` when the published class map contains a denomination. An
  unknown class is discarded.
- The UI includes layer health, current counts, confidence, stable track IDs,
  and an action timeline. It does not show `cash basket`, `goods`, transaction
  chains, or review queue states in this redesign.

## Validation and Acceptance Criteria

1. Unit tests cover normalized boxes/keypoints, unknown-label rejection,
   duplicate suppression, and ID stability for all model adapters.
2. A startup integration test verifies that one missing model degrades only
   its own layer and leaves the API responsive.
3. Unit tests prove that every action rule requires eight valid frames and
   returns `khong_du_quan_sat` when a required input disappears.
4. A synthetic-frame integration test proves observations and actions appear in
   the delayed-overlay history with neutral labels.
5. The live status response never contains a transaction status or a
   review-required result for a one-camera setup.
6. Before enabling the VND layer in the UI, an operator checks at least ten
   live frames containing clear VND notes. If the model does not detect them,
   its layer remains marked `inference_failed`; no generic fallback is used.

## Explicitly Out of Scope

- Training, fine-tuning, annotation, denomination total calculation, or
  counterfeit verification.
- Person identification, face recognition, customer identity storage, POS
  integration, and payments data.
- Transaction reconstruction, loss detection, accusations, automated alerts,
  and review-case creation from one camera.
- Coins, because the selected published model covers Vietnamese banknotes only.
