# V1 Offline Person Tracking Design

## Goal

Build a small, reliable first version that tracks people in a previously
exported Dahua recording. The operator selects a local MP4 file, the local
machine runs Ultralytics YOLO26 with ByteTrack on its RTX 3060 Ti, and the app
returns a downloadable/playable MP4 with `người #ID` boxes drawn over it.

V1 proves only one capability: stable local multi-object person tracking in a
single continuous video. It does not make a transaction, payment, seller, or
behaviour conclusion.

## Fixed Scope

### Included

- A simple Vietnamese web screen for selecting or dragging in one MP4 file.
- Local-only upload/import, metadata validation, queued processing, progress,
  success, and a safe failure message.
- YOLO26n detection on the local NVIDIA GPU when CUDA is available, restricted
  to COCO class `person`.
- Ultralytics `model.track(..., persist=True, tracker="bytetrack.yaml")` on
  consecutive frames from that one video.
- An H.264/yuv420p output MP4 that preserves the source frame resolution and
  draws person boxes, confidence, and the local tracker ID.
- A summary with the processed frame count, mean inference time, effective
  processing FPS, and the number of local track IDs. These are measurements,
  not promised speed figures.
- A button to download or replay the annotated MP4 once processing is ready.

### Explicitly excluded

- RTSP, WebRTC, MediaMTX, HLS, live video, or automatic archive downloading.
- Pose, hands, cash, goods, zones, seller/customer labels, actions, reports,
  alerts, transaction reconstruction, and training data collection.
- Face recognition, person identity persistence across separate videos, or
  re-identification after a person fully exits the view.
- Reading the Dahua recorder hard disk directly.

## Operator Flow

1. The operator exports the needed time interval from the Dahua recorder with
   SmartPSS as MP4, then opens the V1 page.
2. They press **Chọn video** or drop the MP4 onto the page.
3. The page immediately shows the file name, duration, dimensions, and size.
   It rejects an unreadable or unsupported input before starting AI.
4. The operator presses **Bắt đầu theo dõi người**.
5. The page polls one local job for its stage and measured progress. It never
   displays an invented percentage while the model has no evidence of work.
6. When ready, the source preview and annotated result are available together,
   plus the measured summary. The operator can replay or download the result.

## Architecture

```text
MP4 selected in local browser
        |
        v
FastAPI import endpoint --> private local job directory
        |                       |
        |                       +--> source.mp4 (original, unchanged)
        v
one background worker
        |
        +--> OpenCV/FFmpeg sequential decode
        +--> YOLO26n + ByteTrack, CUDA device 0 when available
        |       person class only, persist=True for this one video
        +--> draw boxes on original-resolution frames
        +--> H.264/yuv420p annotated.mp4
        v
job record + measured metrics
        |
        v
React page: progress, source, annotated result, metrics
```

The app creates one independent tracker instance per job. A local tracker ID
only means that the same visible track was associated across consecutive frames
inside that job. It is never treated as a person's real-world identity.

## Quality and Performance Rules

- The web output stays at the recording's original resolution. The inference
  image size is an independent setting, starting at 960px; normalized boxes are
  rendered back on the original frame.
- V1 processes every decoded frame in order. It does not drop frames or batch
  unrelated frames, because either behaviour harms ByteTrack ID stability.
- The default model is `yolo26n.pt`, chosen for first validation on the RTX
  3060 Ti. The interface reports the CUDA/CPU execution state. CPU fallback is
  allowed for diagnosis but is shown as slow; it is not presented as live-capable.
- Model weights are local after the one-time download. Video bytes and results
  never leave the machine.
- The output codec is browser-compatible H.264/yuv420p. An encode or model
  failure produces a neutral failure state and preserves the original file.

## Data, Safety, and Licensing

- Uploaded recordings, job state, and output files remain in a local,
  application-owned data directory. No RTSP URL or camera credential is used
  or stored by V1.
- The UI uses neutral labels: `người #ID`, `đang xử lý`, `đã hoàn tất`, and
  `không thể xử lý video`.
- Ultralytics YOLO26 weights and framework are AGPL-3.0 by default. Before a
  closed-source or distributed commercial deployment, the operator must choose
  a compatible license or an Ultralytics Enterprise license.

## Acceptance Checks for the First Release

1. A user can select a valid 1080p MP4 and see its metadata before processing.
2. A completed job produces a browser-playable H.264 MP4 with only person
   boxes and numeric local IDs.
3. For an uninterrupted visible person, the ID remains stable between
   successive frames. A person who leaves the scene may get a new ID on return;
   V1 labels this as expected tracker behaviour, not an identity error.
4. The source video output has no hand, cash, goods, zone, action, seller,
   customer, transaction, or review labels.
5. The UI displays real measurements for processing FPS, inference time, and
   GPU/CPU mode from the completed job.
6. Tests cover upload validation, job lifecycle, person-only filtering,
   local-ID rendering, safe error messages, and H.264 result serving. A manual
   acceptance run records the observed processing FPS on the RTX 3060 Ti.

## Deferred Steps

After these checks pass on several exported shop recordings, the next design
decision can be a separate V1.1: importing a selected Dahua archive interval
automatically. Live RTSP and every semantic feature remain separate future
projects, each requiring its own approved design.
