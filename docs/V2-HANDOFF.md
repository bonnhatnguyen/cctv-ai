# V2 handoff — private CCTV AI

Created: 2026-09-10 (Asia/Saigon)

## Continuity and recovery anchors

- Current source thread ID: `01a0899c-b099-7383-81c7-1c9574b7fadd` (`V1`).
- Earlier implementation thread ID: `01a07d2d-4e1b-7732-a4a7-1edc0e427e43` (`V1`).
- Original planning thread ID: `01a07c9f-5685-7660-92ce-cbbb2b4044b1` (`Bắt đầu tạo v1`).
- Original V1 repository remains untouched at
  `C:\Users\Admin\Documents\Codex\2026-09-07\t`
  with its implementation worktree at
  `C:\Users\Admin\Documents\Codex\2026-09-07\t\.worktrees\codex-v1-offline-person-tracking`.
- V2 works only in `C:\Users\Admin\Documents\ChatGPT\CCTV AI`.
- V2 starts from exact V1 commit
  `a7f42faffed1b6e969d0d025ae3809423508bec8`.
- Local rollback tag: `v1-stable-2026-09-10`.
- V2 branch: `codex/v2-offline-person-tracking`.
- No Git remote exists. Do not create one, push, publish, host publicly, or
  transmit recordings/telemetry outside this machine.

The V2 repository contains a separate tracked copy of the V1 code and docs.
Private `data/evidence`, `data/labels`, `data/v1`,
`data/v1-task4-acceptance`, and `backend/weights/yolo26n.pt` were copied locally.
`data/v1-launcher` was intentionally not copied because its PID/port ownership
state belongs to the running V1 directory and must never be reused in V2.

## User verdict at the V1 boundary

- The user considers V1 tracking very good and says it does not miss people in
  their accepted use case.
- The displayed/count summary for local IDs is wrong or misleading. Record this
  as a V2 issue, but **do not change V1 now**.
- The user reports that they do not have a usable/visible shortcut. Treat this
  as an unresolved user-facing acceptance issue even if a `.lnk` exists on one
  Desktop path; verify the shortcut from the user's visible Desktop in V2.
- The user explicitly requires a step-by-step V2. Do not attempt all later
  computer-vision goals in one change.
- The user explicitly forbids technical debt.

## Approved V1 behavior

V1 is an offline, local, private workflow:

1. User selects one recorded MP4.
2. Backend imports it into a private UUID job directory.
3. Local YOLO26n detects COCO class `person` only.
4. Official Ultralytics ByteTrack links detections within that clip.
5. Every decoded frame is annotated as `người #ID · confidence%`.
6. FFmpeg writes a silent H.264/yuv420p MP4 with the source resolution, sample
   aspect ratio, rational CFR and complete frame coverage.
7. Web shows real stages, original/result playback, measured CUDA/device/timing
   fields and a download link.

V1 intentionally excludes RTSP/live video, OCR, face identity, pose, hands,
seller/customer roles, money/goods, action recognition, transaction chains,
suspicious-event detection, model training and reports. These are possible
future milestones, not permission to add them all to V2.

## Longer-term product knowledge

The eventual product goal is to learn from recorded shop CCTV and gradually
detect suspicious action sequences. Examples discussed include taking money
from or putting money into the green money basket, giving change to a customer,
taking goods from designated merchandise zones, and putting money in a pocket.
Seller, customer and goods context will ultimately matter. Vietnamese banknote
denomination recognition is not currently required. These labels require real
video datasets and human review; do not claim such actions from person tracking
alone.

The camera/archive source is a Dahua `DH-XVR1A04` DVR. A private RTSP credential
was previously provided, but V1 abandoned realtime and uses exported recordings.
Do not copy credentials into source control or handoff docs. If a future scoped
milestone requires DVR access, ask the user to provide/confirm it at that time
and keep it outside Git.

User-supplied semantic examples retained in the previous thread include:

- the green basket is the money area; opening/reaching into it can mean taking
  out or putting in money;
- a blue shirt is not itself a seller label;
- the left cabinet/table areas shown by the user are merchandise pickup zones;
- a supplied frame was identified by the user as taking money from the basket;
- another supplied frame was identified as giving change to a customer.

These are sparse examples, not a trained dataset and not enough for automatic
action claims.

## Project structure

- `backend/app/v1/contracts.py`, `settings.py`: V1 schemas/configuration.
- `backend/app/v1/media.py`, `tracker.py`, `pipeline.py`, `cli.py`: probing,
  YOLO26n/ByteTrack, annotation, encoding and CLI.
- `backend/app/v1/storage.py`, `jobs.py`, `worker.py`, `api.py`: private imports,
  SQLite lifecycle, sequential worker and loopback API/media serving.
- `backend/tests/v1/`: tracker/media/pipeline/API/job/launcher tests.
- `frontend/src/trackingApi.ts`, `VideoImport.tsx`, `TrackingResult.tsx`,
  `App.tsx`, `styles.css`: Vietnamese V1 web flow.
- `start-v1.bat`, `scripts/start-v1.ps1`: verified local launcher.
- `docs/v1-acceptance.md`, `docs/v1-user-guide.md`: evidence and operator guide.
- `docs/superpowers/specs/` and `docs/superpowers/plans/`: approved V1 design and
  implementation plan.
- `.superpowers/sdd/.../progress.md`: detailed execution ledger.

## Verified V1 baseline

- Windows, Python 3.12, Torch `2.11.0+cu128`, Ultralytics `8.4.145`, OpenCV
  `5.0.0`, FFmpeg/ffprobe `9.0.1`.
- Actual device: NVIDIA GeForce RTX 3060 Ti via `cuda:0`.
- Model: local `backend/weights/yolo26n.pt`.
- Final verification: backend `81` tests passed; frontend `15` tests passed;
  production build passed.
- Baseline visible-person gate: one target stayed one ID for 125/125 evaluated
  frames with no loss over 0.5 seconds.
- V1 retains `imgsz=960`. Trials at 1280/1920 traded one clip-specific issue for
  other misses and were not adopted.
- Browser/launcher V1 was last verified on backend 8001 and frontend 5174 because
  8000/5173 were already occupied.
- Full measurements, limitations and hashes are in `docs/v1-acceptance.md`.

## Known limitations, stated honestly

- Local track IDs are not identity. They may change after strong occlusion,
  exit/re-entry or detector loss.
- A clip can contain more local IDs than unique people. Therefore the V1
  `Số ID trong clip` field is not a unique-person count and is the first known
  V2 product issue.
- Strongly cropped/edge-only people can be hard for the COCO person detector.
- Output is silent; VFR and odd dimensions are rejected.
- Model/framework licensing is AGPL-3.0 by default; commercial use requires a
  compatible licensing decision.

## No-technical-debt rule for V2

Every V2 milestone must satisfy all of the following before the next begins:

1. Write a bounded design and acceptance gate first.
2. Preserve the V1 tag and original V1 worktree; never develop V2 in them.
3. Add a failing test/evaluation that reproduces the exact problem before the
   implementation change.
4. Use real private shop clips for computer-vision acceptance; mock tests alone
   cannot pass a vision gate.
5. Keep one source of truth for contracts/configuration. Do not duplicate magic
   thresholds or add compatibility hacks without an explicit removal plan.
6. No silent fallbacks, fabricated metrics, swallowed errors, stale process
   reuse, hidden partial outputs or claims unsupported by evidence.
7. Keep security boundaries: UUID storage, resolved-path checks, loopback only,
   local weights and no recordings in Git.
8. Run affected tests plus the complete backend/frontend suites, production
   build, real browser playback and real GPU evidence as applicable.
9. Update acceptance docs and commit one reviewable local milestone.
10. Stop and repair a failing gate; do not stack the next feature on top of it.

## First instruction for the V2 chat

Read this file, `docs/v1-acceptance.md`, the approved V1 spec/plan and the Git
tag before changing code. Confirm the V1 baseline is clean. Then propose **one**
small V2 milestone and its measurable acceptance test. The natural first
candidate is to replace the misleading ID total with explicitly named,
evidence-backed metrics, but do not implement it until the user approves the
exact meaning. Do not start pose/action/money/transaction work in the same
milestone.
