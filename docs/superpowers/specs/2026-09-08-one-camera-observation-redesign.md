# One-Camera Observation Redesign

## Goal

Replace the current generic prompt-based detection pipeline with three
purpose-specific, pre-trained model adapters that display tracked people,
hands, and Vietnamese banknotes on one RTSP camera. The system is an
observation tool only: it must not create a transaction, shortage, theft, or
review decision from this one camera.

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
| Hands | MediaPipe Hand Landmarker | Run in video mode; derive one box from the 21 landmarks and associate a short-lived local hand ID. |
| Vietnamese banknotes | Vietnamese Banknote YOLO11 model by bao-vu on Roboflow Universe | Install/export the published model artefact locally, verify its license and class map, and detect only its published VND classes. |

The current YOLO-World prompt path for `banknote`, `cash basket`, and `goods`
is removed from the live runtime. A model trained for other countries' banknotes
is not an eligible fallback.

## Architecture

```text
RTSP frame (cam-a)
    |
    +-- Person adapter: Ultralytics COCO person model + ByteTrack
    |       -> Observation(kind="person", track_id="cam-a-person-<id>")
    |
    +-- Hand adapter: MediaPipe Hand Landmarker (video mode)
    |       -> Observation(kind="hand", track_id="cam-a-hand-<id>")
    |
    +-- VND banknote adapter: published Vietnamese Banknote YOLO11 checkpoint
            -> Observation(kind="vnd_note", denomination=<published class>)
             -> ByteTrack ID scoped to cam-a
    |
    v
Observation aggregator
    -> delayed overlay aligned with HLS video
    -> model/layer health and per-class counts
    -> append-only diagnostic observations only
```

Each adapter owns model loading, input preprocessing, normalized output boxes,
confidence thresholds, and a neutral availability state. The aggregator is the
only component allowed to combine outputs for the frontend. It may never turn
these observations into `cash_removed_from_basket`, `review_required`, or a
transaction status while only `cam-a` is active.

## Model Loading and Health

On startup, the application validates each configured artefact before opening
the RTSP stream.

- `person`: ready only if the official Ultralytics person model is available.
- `hand`: ready only if the bundled MediaPipe task model is available.
- `vnd_note`: ready only if the exported Vietnamese Banknote model and a
  checked-in, documented class map are available locally.

The live status endpoint returns one state per layer: `ready`, `running`,
`model_unavailable`, or `inference_failed`. The UI shows unavailable layers in
plain language and renders no fabricated boxes. The API must not surface model
paths, RTSP URLs, tokens, or exception text.

## Tracking and Overlay Rules

- Apply class-aware non-maximum suppression before any tracker update.
- Person and VND-note tracks use independent ByteTrack instances. IDs are
  unique within a camera and class namespace.
- Hand IDs are associated only with nearby hand landmarks and expire after a
  short sequence of missing frames.
- The overlay reads the detection history nearest to the active HLS delay.
- Labels are exactly `khách`, `tay`, or `tiền <mệnh giá>` when the published
  class map contains a denomination. An unknown class is discarded.
- The UI includes layer health, current counts, confidence, and stable track
  IDs. It does not show `cash basket`, `goods`, transaction chains, or review
  queue states in this redesign.

## Validation and Acceptance Criteria

1. Unit tests cover normalized boxes, unknown-label rejection, duplicate
   suppression, and ID stability for all three adapters.
2. A startup integration test verifies that one missing model degrades only
   its own layer and leaves the API responsive.
3. A synthetic-frame integration test proves observations appear in the
   delayed-overlay history with neutral labels.
4. The live status response never contains a transaction status or a
   review-required result for a one-camera setup.
5. Before enabling the VND layer in the UI, an operator checks at least ten
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
