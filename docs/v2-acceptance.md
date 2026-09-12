# V2 acceptance ledger

This ledger records local-only evidence. Private recordings, databases, prepared
media, benchmark reports, and labels remain under ignored data roots and are
never staged.

## M1 / PREFLIGHT — 2026-09-10

- Base HEAD: `212b9b145d3fc6e03230978453a2769b5e900ea3`.
- Branch: `codex/v2-offline-person-tracking`.
- `v1-stable-2026-09-10^{}`: `a7f42faffed1b6e969d0d025ae3809423508bec8`.
- `git remote -v`: empty; no remote was created.
- Pre-existing untracked drafts preserved:
  `docs/superpowers/plans/2026-09-10-v2-truthful-local-tracking.md` and
  `docs/superpowers/specs/2026-09-10-v2-truthful-local-tracking-design.md`.
- Dedicated V2 interpreter:
  `C:\Users\Admin\Documents\ChatGPT\CCTV AI\.venv\Scripts\python.exe`,
  copied locally from the verified V1 runtime without changing V1.
- Runtime: Python 3.12.14; FastAPI 0.141.1; Pydantic 2.13.5;
  SQLAlchemy 2.0.52; pytest 9.1.1; Torch 2.11.0+cu128; Pillow 12.3.0;
  OpenCV 5.0.0; Uvicorn 0.52.4.
- CUDA probe: available; `NVIDIA GeForce RTX 3060 Ti`.
- Frontend dependencies restored with `pnpm install --offline
  --frozen-lockfile`: 108 packages reused, zero downloaded.
- Initial `scripts/start-v1.ps1 -CheckOnly` correctly failed because the V2
  runtime/assets were absent. After local setup it passed.
- The first full backend baseline exposed a path-with-spaces launcher defect:
  76 passed / 10 failed. Uvicorn received `AI\backend` as an extra argument.
  Existing launcher tests were the RED reproduction. Quoting the three child
  process path arguments made the focused test pass and the launcher suite
  pass (12 tests). Full backend baseline then passed: 86 tests, with two
  pre-existing dependency deprecation warnings.
- Frontend baseline: 15 tests passed. Production build passed; the existing
  Vite native-config warning remains non-fatal.
- Shop gate clip selected in ignored private data:
  `data/v1/jobs/a63fbbce-34c4-417d-8f95-45306822ebcf/source.mp4`, 30.001 s,
  960x1080, 25/1 fps, 751 frames, SHA-256
  `513c5eeeccd1f519dfdb6d5cf4a5f720448eca58e9345b3d29dd5eb2a8457493`.

## M1 / Task 1 — contracts, persistence, and ROI revisions

- RED 1: annotation tests failed collection because `app.annotation` did not
  exist.
- RED 2: after contracts/geometry were introduced, repository imports remained
  absent as expected.
- RED 3: the migration rollback test showed that `sqlite3.executescript`
  committed DDL outside the intended transaction. Migration statements now run
  individually under one `BEGIN IMMEDIATE` transaction.
- RED 4: reopening the database allowed a dangling clip/setup ROI. Migration
  v1 now declares both foreign keys and every connection enables FK checks.
- GREEN: `pytest tests/annotation/test_contracts.py
  tests/annotation/test_repository.py -q` — 17 passed.
- Implemented strict path-free DTOs, simple-polygon validation, source-root
  binding/fingerprint, one root owner lock, rollback-safe schema migration,
  future-version rejection, idempotent operation replay before revision checks,
  concurrent clip revision protection, immutable ROI/template revisions, and
  keyset clip/setup lists.

Task 1 is a development checkpoint, not M1 acceptance. Exact-frame media, API,
UI, browser evidence, and CUDA regression gates remain pending.

## M1 / Tasks 2–4 — exact media, API, and ROI workspace

- RED/GREEN media path: tests first failed because prepare/frame/worker modules
  were absent. The implemented path streams source decode, writes verified
  one-second FFV1 chunks plus a clean CFR H.264 preview, stores per-frame RGB
  hashes, validates manifests and source identity, and serves exact PNGs under
  bounded queue/deadline/LRU limits.
- Release/reprepare tests keep source/hash/ROI and revision history. Startup
  now converts crash-left `preparing` records to explicit
  `preparation_interrupted` failures and removes only staging inside the owned
  annotation root.
- API tests cover imported-job annotation without starting tracking, UUID/frame
  boundaries and path-free responses. Backend DTO JSON Schema and TypeScript
  are generated offline; frontend build checks drift before TypeScript/Vite.
- UI tests cover stale response/image tokens, SAR geometry, revision-conflict
  draft retention, dirty clip switching, explicit camera-template confirmation,
  vertex drag mapping and the fixed preview speeds 0.25/0.5/1.
- Browser workflow on Chrome displayed the 960x1080, SAR 2:1 shop source at
  its correct 16:9 display aspect. Exact frames 0, 375 and 750 loaded with the
  matching response token. Camera `CAM 2 - rổ tiền` and a four-point normalized
  ROI were saved around the fixed basket area. Reload and a full owned-service
  restart preserved frame selection, camera and ROI.
- Real release moved the clip `ready → releasing → released`, reduced
  `prepared_bytes` from 536,159,684 to zero, and kept the source hash and ROI.
  Explicit retry returned to `ready` with the same frame count/hash/ROI.

## M1 / Task 5 — real shop gate (in progress)

- Isolated instance: backend `127.0.0.1:18001`, frontend
  `127.0.0.1:18002`, source/annotation root
  `data/v1/m1-acceptance` (private and ignored).
- Source job `0b84043b-1e7b-42d6-b39e-6d9c16cc3813`; annotation clip
  `c097c6a7-8837-486b-a49f-26b27739f0fe`. Preparation took about 11 seconds
  initially and produced 536,159,684 bytes from a 7,960,691-byte source
  (67.35×; 511.3 MiB), within the 20 GiB configured quota.
- CUDA tracking processed 751/751 frames on `cuda:0`, device
  `NVIDIA GeForce RTX 3060 Ti`, mean inference 5.862 ms, effective 33.991 fps.
  Private JSONL evidence contains two distinct local track IDs and equals
  `summary.local_track_count == 2`.
- The first measured browser run exposed adjacent p95 391.5 ms because every
  frame decoded the same FFV1 chunk again. A RED regression test now requires
  adjacent requests in one chunk to invoke one decoder. Chunk-wide verified
  PNG caching fixed the cause without changing pixels or raising the 512 MiB
  LRU budget.
- Passing shop report: private run
  `0f0a7893-328e-44f7-bf50-ad87d92caf03`, Chrome 153.0.8010.36,
  Playwright 1.62.1. All 70/70 browser images matched an independent sequential
  source decode by RGB SHA-256. Adjacent p95 = 45.8 ms (limit 250 ms); far-seek
  p95 = 1,879.5 ms (limit 2,000 ms). Verification report verdict is `PASS`.

- Ten-minute CFR fixture: 600.000 s, 640x360, 25 fps, 15,000 frames, SAR 1:1,
  source 130,282,036 bytes. Preparation completed in 106.75 seconds and used
  945,197,591 bytes (7.25×; about 90.14 MiB/minute). Private browser run
  `3ff52732-41ce-40b3-9ba7-15ef22c6c2ae` matched 70/70 RGB frames;
  adjacent p95 = 41.1 ms and far-seek p95 = 225.6 ms. Both latency gates PASS.

## M1 / Final regression and review — 2026-09-11

- Contract drift check, Python compile check and `git diff --check`: exit 0.
- Full backend regression: 124 passed, 0 failed in 190.99 seconds. Two warnings
  come from the installed FastAPI/Starlette test-client dependency aliases;
  application routes do not emit a new deprecation warning.
- Full frontend regression: 8 files / 28 tests passed. Production build passed
  after the annotation contract check; Vite still prints the documented native
  config-loader warning, with no build failure.
- A full-suite RED exposed an old launcher fixture that combined a backend from
  data root B with a launch request for data root C. Reusing it would violate
  the new root fingerprint boundary. The fixture now supplies root B explicitly
  while preserving its original stale-frontend replacement assertion; focused
  launcher test passed in 36.57 seconds and the full suite then passed.
- Manual implementation audit found and fixed two lifecycle risks before the
  final run: shutdown now cancels and waits for an owned media subprocess, and
  repeated cleanup failure no longer creates a hot loop of clip revisions.
  Both have RED/GREEN regression tests.
- A requested independent code-review agent could not run because the workspace
  reported no review credits. No independent-review verdict is claimed. The
  fallback was a line-by-line plan/diff audit plus all automated, process,
  browser, media-hash and private-video gates recorded above.
- Final local restart on the bound acceptance root passed annotation health and
  reopened the shop clip as `ready`, source `available`, clip revision 6 and ROI
  revision 1. Headless Chrome then opened the list, displayed storage usage and
  loaded exact frame 0.

**M1 verdict: PASS locally.** M1 provides clip/frame/ROI tooling only. Action
labels `hand_in`, `hand_out`, `take_out`, `put_in`, `unclear`, pilot review and
training export remain M2/M3 and were not implemented here.

## M2 / action labeling software gate — 2026-09-11

- Base implementation starts after `073f80a`. The action workspace now follows
  the fixed order **Vùng rổ → Gán nhãn → Kiểm tra** and reuses the M1 player,
  exact-frame state and saved ROI instead of introducing a second video state.
- The UI creates/selects hand interactions and supports the exact five labels,
  I/C/O frame capture, 1–5 label shortcuts, Ctrl+S, Escape, conditional unclear
  metadata, editing, confirmation, tombstone deletion/restoration and a
  non-merged multi-row timeline. Tracking remains optional.
- Lost-response retries reuse the caller-owned operation UUID only while the
  payload is unchanged. Stale revisions reload server state while retaining the
  draft. If the ROI revision changed in another tab, every load/mutation adopts
  the matching latest ClipView and visible polygon, clears old action/coverage
  frame marks and requires review against the new ROI before saving.
- **Kiểm tra** now records explicit coverage for an exact interval and chosen
  clear-action classes only after an operator attestation. Playback history and
  event confirmation never create background. Unreviewed footage is displayed
  as unknown; invalidated coverage remains visible as audit history.
- The local pilot verifier validates exactly 20 preselected segments, counts
  actual examples in both passes, rejects unknown as no-action, binds results
  to a deterministic label-free shuffled schedule, requires the second pass to
  start at least 24 hours after pass one completed, and computes inclusive IoU,
  maximum one-to-one matches and crossing error with deterministic temporal
  tie-breaking. It never uploads video or labels.
- Real in-app browser on `127.0.0.1:18002` reopened the private shop clip and
  displayed its saved ROI and confirmed `put_in` event after reload. Review mode
  displayed zero pending events, explicit unknown coverage state and disabled
  submit before selection/attestation. Delete → restore → confirm completed on
  the real API and returned the event to its original confirmed state. No fake
  coverage or additional action label was written to the private clip.
- Full frontend regression before final commit: 14 files / 57 tests passed;
  contract drift, TypeScript and production Vite build passed. Full backend
  regression after the final verifier audit: 154 tests passed in 209.07
  seconds. Python compile and `git diff --check` passed. The existing
  Vite native config-loader warning and two installed test-client deprecation
  warnings remain non-failing and unchanged.
- Independent Astra/Pascal review found and drove fixes for cross-clip response
  races, dirty-draft loss, operation replay, stale revision/ROI adoption,
  coverage attestation reset and false pilot PASS cases. The final tie-break
  audit found no remaining blocker after adversarial first-side, second-side
  and permuted-input tie probes.

**Pilot evidence verdict: PENDING_DATA.** The current private data contains one
confirmed `put_in` event, not a preselected 20-segment pilot with two real
examples per required class and two passes separated by 24 hours. This does not
block the M2 software tool, but it blocks any claim that guideline consistency
or action-model training data is ready.

## Clip-bound playback correction — 2026-09-11

- Base HEAD: `c71ab55`. Frontend-only correction; no DB migration, encoder,
  tracking model, source video, or saved ROI mutation.
- RED: regression tests reproduced missing saved ROI on tracking players,
  preview starting at zero instead of the selected frame, and the absent
  selected-clip tracking action. A further RED caught preview shortcuts
  intercepting keyboard actions in the newly embedded tracking section.
- The V2 workspace now loads a tracking result by the selected clip's
  `source_job_id`, on explicit click, using the existing read-only V1 endpoint.
  This does not replace the V1 active job. Results from a previous selection
  are cancelled/discarded. Missing, mismatched, network-error and imported-job
  cases have explicit UI states; no tracking is started by lookup.
- Saved ROI is visible during annotation preview and on the two linked
  tracking players. Source/job mismatch or unavailable source suppresses the
  overlay. Display geometry reuses the normalized raster/SAR contain helper.
  Preview resumes at selected frame time; frame selection pauses and returns
  to exact images. Editing vertices remains disabled during playback.
- Fresh frontend regression: 9 files / 36 tests passed. Contract drift check,
  TypeScript and production build passed. The existing Vite native-config
  warning remains. Backend tests were not repeated for this frontend-only
  change; prior backend/CUDA evidence above is historical.
- Real in-app browser at `127.0.0.1:18002`: V1 initially displayed the 15-second
  excerpt (`e7fdc30c-4c2d-4c0a-ae94-d5e67d1bfc16`). Selecting `shop-30s.mp4`
  in V2 opened result job `0b84043b-1e7b-42d6-b39e-6d9c16cc3813`, with its
  saved ROI visible in both source/result players. Result playback worked.
  Preview selected frame 375 started at approximately 15.17 seconds; after
  pausing at frame 522, replay began at approximately 21.05 seconds, consistent
  with 522/25 plus elapsed playback. The 960x1080 SAR 2:1 source and ROI were
  visually inspected together.
- Switching to the released ten-minute fixture removed the prior result and
  reported that the fixture had not run tracking. Returning to V1 restored the
  original excerpt result, with zero ROI overlays from the other clip.
- Scope: ROI is a browser overlay, not burned into downloaded MP4 or native
  video-only fullscreen. This correction does not implement M2 action labels.

## Assisted-label benchmark / software gate — 2026-09-12

- Chặng A is isolated from V1/V2 runtime and annotation writes. The CLI freezes
  a read-only clip/source/ROI/reference snapshot with SHA-256, rejects remote
  inputs, verifies pinned model assets and writes only beneath the bound private
  `annotations/benchmarks` root.
- A run uses one model in an owned child process, a per-root Windows lock,
  offline model settings, deadline/process-tree cleanup, disk quota/reserve,
  exact scheduled-frame JSONL (including empty detections), stale-state checks
  before and after inference, and atomic no-overwrite publication. Failed runs
  remain explicitly failed and never become zero-event successes.
- The current private database has four clips, three saved ROIs, zero review
  coverage rows and no active action annotations. Its only historical action is
  a deleted draft `put_in`; it is not reinterpreted as `hand_in` or `hand_out`.

**SOFTWARE:** focused benchmark verification passed: 74 tests, 3 optional model
tests skipped; final whole-project regression is recorded below.
**MODEL_SMOKE / MediaPipe:** PASS as a technical execution gate. Private run
`ad3535b3-4b54-44bc-b839-001caa1aa304` processed all 151 scheduled frames from
the frozen 30-second shop selection (stride 5), detected one hand on one frame
and emitted one review-only `track_gap` proposal. Process model load was 0.485 s,
inference 1.719 s and end-to-end 4.984 s. This sparse result is not a quality
PASS.
**MODEL_SMOKE / Grounding DINO:** PASS as a technical execution gate. Private
run `ac8eca20-64cb-4324-a1cc-8d8b3328ce25` used the same frozen selection,
processed all 151 frames, produced 131 detections on 96 frames and emitted 33
review-only proposals (21 `association`, 12 `track_gap`). Process model load
was 6.501 s, synchronized CUDA inference 85.128 s and end-to-end 95.828 s. The
larger noisy review queue is not a quality PASS.
**QUALITY:** `PENDING_DATA` — no confirmed `hand_in`/`hand_out` references or
class-specific reviewed coverage exist.
**EFFORT:** `PENDING_DATA` — no paired manual/assisted timed review exists.

Final regression: the isolated benchmark environment passed 74 tests with 3
explicit optional model-smoke skips, and Python compile passed. The unchanged
application runtime suite passed 154 tests with its two existing dependency
deprecation warnings. Frontend passed 14 files / 57 tests, annotation contract
check, TypeScript and production build; the existing Vite native-config warning
remains non-failing. The first isolated test run exposed missing SQLAlchemy in
the benchmark lock (model execution itself worked); SQLAlchemy 2.0.52 and its
greenlet dependency were added with hashes, setup was rerun, and the exact
isolated command then passed.
The final repeated frontend suite exposed that ROI-conflict coverage frame marks
were cleared in an effect one render after the warning. Keying the review panel
to the reset revision now clears those marks in the same adopted-ROI render;
the focused regression and the full 14-file / 57-test suite then passed.
