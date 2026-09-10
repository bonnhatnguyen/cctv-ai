# V1 acceptance record

Evidence date: 2026-09-10, Asia/Saigon. Repository and all media processing
remained local. No remote was created, no push/publish/cloud action occurred,
and no recording was transmitted outside loopback. Private artifacts under
`data/evidence`, `data/v1`, `data/v1-task4-acceptance`, and
`data/v1-launcher` are ignored by Git.

## Commit ledger

| Task | Base | Head | Gate |
|---|---|---|---|
| 1 — first real tracking video | `3ad9b2219ee45456827a61fdb27f73c476452f9c` | `087b5fa740356fe152cafff50f402d827963d8c3` | PASS |
| 2 — media validation/playback | `087b5fa740356fe152cafff50f402d827963d8c3` | `fc72b80c481cec24514458b9f050363963abcc41` | PASS; multi-clip continuity assessed below |
| 3 — local import/job API | `fc72b80c481cec24514458b9f050363963abcc41` | `2f4835acebf720e3a8cf24bbb8b2c9407406ccda` | PASS |
| 4 — focused Vietnamese UI | `2f4835acebf720e3a8cf24bbb8b2c9407406ccda` | `5c52882b218bb4b1a8210ab819e687cf9d1ccbdd` | PASS |
| 5 — verified local launcher | `5c52882b218bb4b1a8210ab819e687cf9d1ccbdd` | current branch | PASS for startup/workflow/recovery and recorded continuity assessment |

## Task 1

Files: `backend/app/v1/{cli,contracts,media,pipeline,settings,tracker}.py`, V1
tests and tracker YAML. Exact implementation/review detail is retained in the
ignored `task-1-report.md`.

Final commands and results:

```text
PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests/v1 -q
16 passed

PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests -q
35 passed, 2 dependency deprecation warnings
```

Real artifact: `data/evidence/tracked-excerpt-20-35.mp4`; CUDA device
`cuda:0 NVIDIA GeForce RTX 3060 Ti`; 375/375 frames; 960x1080, SAR 2:1,
H.264/yuv420p, 15.000 s; 8 local IDs; mean inference 13.076 ms; total
processing 20.302 s; 18.471 FPS. Output SHA-256
`9D202E507CE67A1E79A1F7A8F36B74456BF7B9E71969B76AA8823F25425A1691`.
The selected gray-top target was ID 1 in 125/125 evaluated frames with zero
switches and no loss longer than 0.5 s. Source SHA-256 remained
`168C34E8FECEB308499EDE039399BC33C887EFF4BD5A83445204140E05B167C2`.

## Task 2

Files: V1 media/pipeline hardening and their FFmpeg-backed tests. Exact
implementation/review detail is retained in `task-2-report.md`.

```text
PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests/v1 -q
25 passed in 3.64s

PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests -q
44 passed, 2 dependency deprecation warnings in 4.12s
```

The 15-second output passed full browser playback and explicit Range-backed
seeks at 0, 7.5 and 14.5 seconds. Two additional retained outputs A/B fully
decoded with original resolution and playable H.264/yuv420p. Their contact
sheets/JSONL demonstrate real boxes and IDs. The final continuity assessment
and limitations are recorded in the final section of this document.

## Task 3

Files: `backend/app/v1/{api,database,jobs,storage,worker}.py`, settings,
pipeline cleanup and V1 API/job tests. Exact implementation/review detail is
retained in `task-3-report.md`.

```text
PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests/v1/test_api.py -q
13 passed, 2 dependency deprecation warnings in 2.22s

PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests/v1 -q
51 passed, 2 dependency deprecation warnings in 11.85s

PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests -q
70 passed, 2 dependency deprecation warnings in 12.99s
```

Real HTTP → worker → YOLO26n/ByteTrack evidence: 375 frames, CUDA RTX 3060 Ti,
10 IDs, mean inference 5.9311 ms, total processing 13.6843 s, 27.4037 FPS,
15.000 s H.264/yuv420p output, Range 206, download 200, source preserved.

## Task 4

Files: `frontend/src/{App,App.test,VideoImport,TrackingResult,trackingApi}.tsx`
and styles. Exact implementation/review detail is retained in
`task-4-report.md`.

```text
pnpm exec vitest run --reporter=verbose
2 files passed; 13 tests passed

pnpm run build
19 modules transformed; exit 0
```

Real browser job `fbba17e0-4a78-4dfb-bb6e-56992fd3c57f` ran on CUDA, rendered
truthful progress, played/searched the 15-second source/result, downloaded the
result and restored after reload. Source SHA-256
`62774F0223B4C9DE75077CCABEE5D2786FD06FCC2B4553632242443CF2965984` was
unchanged; output SHA-256
`1D9A8D67139AACC5529CEA88DEB81B43280916400F71B4677747B2812E6D6AD4`.

## Task 5

Files: `start-v1.bat`, `scripts/start-v1.ps1`, launcher tests, instance-aware
health, Vite runtime identity/dynamic loopback proxy,
`frontend/public/v1-service-identity.json`, `.gitignore`, this record and
`docs/v1-user-guide.md`. `start-local.bat` has no diff from the Task 5 base.

### TDD and automated verification

Initial RED before launcher implementation:

```text
cd backend
..\.venv\Scripts\python.exe -m pytest tests/v1/test_launcher.py -q
4 failed in 0.48s
```

The failures were the intended missing behaviors: no `start-v1.bat`, no
PowerShell preflight, no exact frontend identity document and a retained legacy
`/live` proxy. A separate health RED failed with `KeyError: instance_id`.
The dynamic Vite proxy RED then failed because `V1_BACKEND_PORT` was absent.

Final fresh verification:

```text
cd backend
..\.venv\Scripts\python.exe -m pytest tests/v1 -q
56 passed, 2 dependency deprecation warnings in 11.64s

..\.venv\Scripts\python.exe -m pytest tests -q
75 passed, 2 dependency deprecation warnings in 12.10s

cd ..\frontend
pnpm test -- --run
2 files passed; 13 tests passed in 1.19s

pnpm build
19 modules transformed; exit 0

PowerShell parser + .\scripts\start-v1.ps1 -CheckOnly
syntax PASS; Python 3.12 project .venv, pnpm, packages, model, FFmpeg,
ffprobe and Vite verified; exit 0

git diff --check
exit 0 (Windows LF/CRLF conversion notices only)
```

The two Python warnings are the pre-existing Starlette/httpx and AnyIO
deprecations. Vite emits the pre-existing forward-looking ESM/native-config
warning; tests/build succeed.

### Launcher, occupied ports and restart

At acceptance time unrelated/pre-launcher services occupied loopback ports
8000 and 5173. Their PIDs remained untouched. The launcher selected backend
8001 and frontend 5174, verified both identities/readiness, and wrote separate
stdout/stderr logs plus owned PIDs under `data/v1-launcher`. A second invocation
reported that it reused the verified 8001/5174 pair.

Active-restart job `af047a7b-b038-4bb4-8ae6-e4b4d786f689` reached
`processing/loading`. `start-v1.ps1 -Stop` stopped only recorded matching PIDs;
the prior 8000/5173 listeners remained. After restart, the job truthfully read
`failed/failed`, code `xu_ly_bi_gian_doan`, and its source still hashed
`62774F0223B4C9DE75077CCABEE5D2786FD06FCC2B4553632242443CF2965984`.
Abrupt termination left private `.tmp` files in that failed job directory;
they are not exposed or treated as READY, but cleanup of those remnants is a
documented limitation.

### Three fresh shop runs

| Clip / job | Source → output | Real execution | Timing | Track/browser evidence |
|---|---|---|---|---|
| 15 s / `100bf7f3-7079-4f82-b702-f6fd6fce08f7` | 960x1080 15.000 s → 960x1080 15.000 s, 375 frames, H.264/yuv420p | `cuda:0`, RTX 3060 Ti | mean inference 5.6562 ms; total 12.7136 s; 29.4961 FPS | 10 local IDs; source preserved; Range 206 (900 bytes), download 200 |
| A / `a63fbbce-34c4-417d-8f95-45306822ebcf` | 960x1080 30.001 s → 960x1080 30.040 s, 751 frames, H.264/yuv420p | `cuda:0`, RTX 3060 Ti | mean inference 6.3638 ms; total 20.2835 s; 37.0252 FPS | 2 local IDs; source preserved; contact sheet shows IDs 1/12, full continuity annotation pending |
| B / `631ca872-100b-4d7d-be0c-4183c2130c56` | 960x1080 25.001 s → 960x1080 25.040 s, 626 frames, H.264/yuv420p | `cuda:0`, RTX 3060 Ti | mean inference 6.3726 ms; total 16.5945 s; 37.7233 FPS | 12 local IDs; source preserved; sampled frames include misses/multiple IDs, full continuity annotation pending |

Fresh browser workflow job `90607ac2-de67-4200-a0ec-7bb13cf62dd0` completed
from the launcher-started UI: 375 frames, 10 IDs, CUDA RTX 3060 Ti, mean
inference 6.4378 ms, total 10.9554 s and 34.2298 FPS. The tracked result played
continuously to `ended=true`, `currentTime=duration=15`, `readyState=4`, no
media error. Native seeks at 0, 7.5 and 14.55 s returned readyState 4; screenshots
showed aligned boxes and Vietnamese labels. A real browser download event was
received and reload restored the READY job.

Fresh direct browser checks opened A/B through their Range-capable result
endpoints. A decoded at 30.04 s with readyState 4, visually showed ID 12, and
seek-to-end returned `ended=true`; B sought to 12.52 and 25.04 s with
readyState 4/no media error. This proves browser decode/seek, not a complete
human-annotated continuity pass.

### Gate and limitations

Startup, exact identity/repeat reuse, safe occupied-port behavior, real CUDA
tracking, truthful progress, validated media, browser playback/seek/download,
reload restoration and interrupted-job recovery pass. The required limitation
remains explicit: clips A/B do not yet have a complete frame-by-frame continuity
annotation, so no multi-clip ID-accuracy claim is made. CPU-only diagnosis and
mock tests were not substituted for the RTX/browser gate.

Ultralytics YOLO26/framework/default weights are AGPL-3.0 by default; a
compatible license or Ultralytics Enterprise license is required for uses that
are not compatible with AGPL-3.0. The operator notice is retained in
`docs/v1-user-guide.md`.

### Task 5 review hardening — 2026-09-10

Review base: `8566a40`. Fix head: the commit containing this section.

The first behavioral launcher run against the reviewed implementation was RED:
four lifecycle cases failed because the launcher did not yet accept isolated
state/data/port inputs. A later focused RED exposed the remaining process-start
window exactly:

```text
.\.venv\Scripts\python.exe -m pytest backend\tests\v1\test_launcher.py -q -k starting_child_ownership
1 failed, 6 deselected in 7.78s
AssertionError: observed_backend is None
```

Launcher state is now schema 2 and records a GUID generation plus exact
launcher/listener PID, parent PID, creation time, executable and command line.
It verifies the project-root invocation and loopback listener ancestry before
reuse or termination. State moves atomically from `starting` to `ready` for
each service. A failed launch removes only services created by that invocation;
a previously verified backend is retained. Termination is checked before the
corresponding state entry is removed. A PID reused by an unrelated process is
rejected and the state file is retained unchanged.

Frontend runtime identity now includes the selected `backend_url` and
`backend_port`. Reuse additionally requires a successful backend health request
through the frontend `/api` proxy. A stale frontend is replaced only after its
full launcher ownership is verified. The obsolete static identity JSON was
removed so the dynamic, target-aware endpoint is the single launcher identity.

Fresh behavior and gate verification:

```text
.\.venv\Scripts\python.exe -m pytest backend\tests\v1\test_launcher.py -q
7 passed in 103.85s

.\.venv\Scripts\python.exe -m pytest backend\tests\v1 -q
59 passed, 2 dependency deprecation warnings in 113.15s

.\.venv\Scripts\python.exe -m pytest backend\tests -q
78 passed, 2 dependency deprecation warnings in 113.44s

cd frontend
pnpm test -- --run
2 files passed; 13 tests passed in 1.18s

pnpm build
19 modules transformed; exit 0

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-v1.ps1 -CheckOnly
project .venv Python, pnpm, imports, model, FFmpeg/ffprobe and Vite verified; exit 0

PowerShell parser
PASS
```

The seven launcher tests use temporary state/data directories and dynamically
reserved loopback blocks at port 18000 or above. They cover launch/repeat/stop,
proxy-target identity, replacement of an owned stale frontend, unrelated Vite
PID reuse, partial-start rollback, preservation of a reused backend, and atomic
`starting` ownership. They clean up only their own recorded process trees. The
existing user-facing listeners on 8000/5173/8001/5174 were not stopped or used
by these tests. No remote, publish, cloud, telemetry or media transmission was
performed. Existing RTX/browser/video evidence above remains unchanged because
this fix round changed only launcher lifecycle/identity and operator docs.

The user guide now states the actual input limits (VFR and odd dimensions are
rejected) and that annotated H.264 output is silent. Its Ultralytics
AGPL-3.0/Enterprise licensing notice remains explicit.

### Task 5 review hardening round 2 — Windows PowerShell compatibility

Review base: `bc1d92c`. Fix head: the commit containing this section.

`start-v1.bat` invokes Windows `powershell.exe`, whose PowerShell 5.1 / .NET
Framework runtime provides only the two-argument `System.IO.File.Move` overload.
The previous three-argument call was therefore incompatible even though the
newer `pwsh` runtime used by the original tests accepted it.

The launcher behavior harness now resolves only `powershell.exe` and verifies
that this is the runtime named by the batch launcher. The focused RED reproduced
the actual double-click failure on Windows PowerShell 5.1.26100.9444:

```text
.\.venv\Scripts\python.exe -m pytest backend\tests\v1\test_launcher.py -q -k repeat_launch
1 failed, 6 deselected in 11.09s
V1 startup failed: Cannot find an overload for "Move" and the argument count: "3".
```

Atomic state writes now use same-directory temporary and backup files. Initial
creation uses the PowerShell 5.1-compatible two-argument `File.Move`. When state
already exists, four-argument `File.Replace` atomically swaps it while retaining
the old state as a unique backup until the successful call returns; `finally`
removes any temporary/backup remnants. There is no delete-before-move interval.
The failed RED launch rolled back its isolated backend and left no launcher test
process behind.

Fresh Windows PowerShell 5.1 verification:

```text
.\.venv\Scripts\python.exe -m pytest backend\tests\v1\test_launcher.py -q
7 passed in 124.12s

.\.venv\Scripts\python.exe -m pytest backend\tests\v1 -q
59 passed, 2 dependency deprecation warnings

.\.venv\Scripts\python.exe -m pytest backend\tests -q
78 passed, 2 dependency deprecation warnings in 135.50s

cd frontend
pnpm test -- --run
2 files passed; 13 tests passed in 1.23s

pnpm build
19 modules transformed; exit 0

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-v1.ps1 -CheckOnly
project .venv Python, pnpm, dependencies, model, FFmpeg/ffprobe and Vite verified; exit 0
```

Lifecycle tests again used only temporary state/data and loopback ports at
18000 or above. Existing user-facing listeners on 8000/5173/8001/5174 were not
stopped or reused. No remote, publish, cloud, telemetry or media transmission
occurred; the prior real RTX/browser/video evidence remains unchanged.

### Final V1 hardening and continuity assessment

The final audit found and fixed launcher recovery after a fully dead recorded
backend, frontend, or both. The launcher validates both ownership records before
changing state, clears only a component whose recorded processes and listener
are all absent, and refuses a dead record when an unrelated listener occupies
its port. The existing user-facing 8001/5174 pair was safely adopted from the
previous launcher state format by verifying process ancestry, project identity,
loopback service identity and frontend-to-backend proxy identity. The desktop
shortcut then opened the verified `http://127.0.0.1:5174/` V1 page.

Interrupted worker recovery now removes only the two worker-owned temporary
files inside the exact persisted job directory. Source and completed artifacts
are not removed. Source preview support is reported conservatively as H.264
plus yuv420p; the UI falls back to an unsupported-preview message after an
actual player error and labels CPU execution as diagnostic fallback.

Fresh final automated verification:

```text
PYTHONPATH=backend .venv\Scripts\python.exe -m pytest backend/tests -q
81 passed, 2 dependency deprecation warnings

cd frontend
pnpm exec vitest run
3 files passed; 15 tests passed

pnpm run build
19 modules transformed; exit 0

PYTHONPATH=backend .venv\Scripts\python.exe -m pytest \
  backend/tests/v1/test_launcher.py backend/tests/v1/test_jobs.py \
  backend/tests/v1/test_media.py -q
33 passed in 231.22s
```

Continuity was reviewed from every JSONL frame plus sampled annotated contact
sheets in `data/evidence/continuity-audit/`:

- Baseline clip: the preselected fully visible target remains the same ID for
  all 125/125 evaluated frames, with no switch or loss over 0.5 seconds. This
  remains the V1 accuracy gate and passes.
- Clip A: the navy-shirt target is ID 12 continuously from frame 271 through
  750 except frames 269–270 before the evaluated run. The black/red target has
  short detection losses and leaves without a reliable full-body box.
- Clip B: the purple-shirt target is ID 1 through frame 109 and is assigned ID
  34 for frames 108–119 near its downward exit, an observed within-view switch.
  The black/red target is heavily cropped/occluded beside the right table and
  has no ID for frames 162–357 (196 frames, about 7.84 seconds), then is detected
  again as a new local ID. These are YOLO26n person-detection limitations, not
  hidden as tracker success.

A controlled 1920 inference-size rerun did not improve this footage and reduced
detections; a 1280 rerun removed the purple target's short ID switch but missed
more of the cropped black/red target. The accepted V1 therefore retains the
tested 960 setting instead of trading one clip-specific failure for another.
V1 passes its scoped gate for clearly visible people, while cropped, strongly
occluded or edge-only people remain a documented limitation.
