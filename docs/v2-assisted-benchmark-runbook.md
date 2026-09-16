# V2 assisted-label benchmark runbook

This is a private command-line technical benchmark. It proposes review spans
for `hand_in` and `hand_out`; it never writes action labels or coverage to the
annotation database. V2 now also has a **Model hỗ trợ** panel, but that is a
separate review queue rather than a benchmark artifact. Both paths reuse the
same adapters and shared inference lease; neither path creates ground truth.

## 1. Prepare models once

From the repository root:

```powershell
./scripts/setup-assisted-benchmark.ps1 -DownloadModels
```

Use the isolated `.venv-assist-benchmark`. Model files stay under
`data/v1/assisted-models/` and are ignored by Git. MediaPipe telemetry is
enabled only because the operator explicitly allowed its documented telemetry;
shop video and annotations remain local inputs.

## 2. Select footage before prediction

Create a private `selection.json`. Frame numbers are zero-based and inclusive.
Do not choose a segment after seeing model proposals. Use `exploratory` when
recording provenance or scenario coverage is unknown.

```json
{
  "schema_version": 1,
  "segments": [{
    "segment_id": "NEW-UUID",
    "clip_id": "CLIP-UUID-FROM-V2",
    "span": {"start_frame": 0, "end_frame": 250},
    "partition": "exploratory",
    "parent_recording_id": null,
    "recording_days": [],
    "provenance_confirmed": false,
    "scenario_tags": ["unknown"]
  }]
}
```

For quality comparison, tag real scenarios among `entry`, `exit`, `stationary`,
`near_pass`, `boundary_jitter`, `multiple_hands`, `occlusion` and `no_action`.
Tuning and evaluation footage must have confirmed provenance and must not share
a recording, source hash or recording day.

## 3. Freeze and validate

From `backend/`:

```powershell
../.venv-assist-benchmark/Scripts/python.exe -m app.annotation_benchmark.cli freeze `
  --source-root ../data/v1/m1-acceptance `
  --annotation-root ../data/v1/m1-acceptance/annotations `
  --selection ../data/v1/m1-acceptance/annotations/benchmarks/selection.json
```

The command prints the immutable manifest path. Keep its sibling `.sha256`.
Create a private config file, for example
`{"schema_version":1,"stride":5,"device":"cpu"}`. Then validate one model:

```powershell
../.venv-assist-benchmark/Scripts/python.exe -m app.annotation_benchmark.cli validate `
  --manifest MANIFEST.json --config CONFIG.json `
  --model mediapipe --model-root ../data/v1/assisted-models
```

`validate` rejects a changed manifest or model weight. `run` additionally
rehashes the source and checks the current ROI, clip revision, reference events
and coverage before and after inference.

## 4. Run one model at a time

Wait for queued/running V1 tracking to finish; the benchmark reports `v1_busy`
and never stops V1 itself.

```powershell
../.venv-assist-benchmark/Scripts/python.exe -m app.annotation_benchmark.cli run `
  --manifest MANIFEST.json --config CONFIG.json --model mediapipe `
  --source-root ../data/v1/m1-acceptance `
  --annotation-root ../data/v1/m1-acceptance/annotations `
  --model-root ../data/v1/assisted-models
```

Repeat with `--model dino` and a CUDA config only when CUDA is available. Each
run uses an owned child process, Windows Job Object cleanup, offline model
settings, a deadline and a one-run-per-root lock. Completed outputs move
atomically to `annotations/benchmarks/runs/<run-uuid>/`. Failures remain under
`failed/` with an explicit status; retry uses a new UUID. Nothing is auto-deleted.
`artifacts.sha256.json` binds every completed input/output artifact; evaluation
rejects a changed file or a run moved outside its recorded private root.

Default output quota is 512 MiB and free-disk reserve is 2 GiB. Model/cache size
is separate. Every scheduled frame has a JSONL row, including frames with zero
detections; missing rows fail the run.

## 5. Review proposals

`proposals.json` is a shortlist, not ground truth. For benchmark runs, open the
matching clip and inspect each `view_span`. For ordinary labeling, create a run
directly from **Model hỗ trợ**, then use or reject each persisted suggestion.
In both cases inspect exact frames, correct `hand_in`/`hand_out`, and review
footage outside proposal spans. Only the operator writes labels and explicit
review coverage. Empty proposals never mean “no action”.

UI assistance freezes source/ROI/guideline, device, stride, config hash and
model-manifest hash when queued. It verifies source bytes before inference and
again before publish, rejects a child schedule or proposal outside the requested
range, persists monotonic progress, and keeps bounded checksummed private
artifacts under `annotations/assistance/runs/<run-uuid>/`. Changing source or
ROI makes unreviewed proposals stale. Run history is newest-first and paginated;
start/cancel/reject retries reuse their operation ID after an uncertain network
failure. The annotation database stores the per-run stride in schema v4, so a
queued run does not silently adopt a later global sampling setting.

Non-crossing reasons (`boundary`, `track_gap`, `association`, `clip_boundary`)
are review-only and carry no predicted label. MediaPipe handedness is not a
detection confidence or stable hand identity.

## 6. Evaluate quality and effort

```powershell
../.venv-assist-benchmark/Scripts/python.exe -m app.annotation_benchmark.cli evaluate `
  --run RUN-DIRECTORY --effort EFFORT.json
```

Omit `--effort` until a timed review exists. Reports are new checksummed files
under `annotations/benchmarks/reports/<run-uuid>/`; existing reports are never
overwritten. Without confirmed `hand_in` and `hand_out` references plus
class-specific full coverage, quality stays `PENDING_DATA`; false negatives and
time savings must not be inferred.

For effort, freeze comparable segments first. Split them manual-first and
assisted-first, reverse the order on the next batch, use the same operator, and
record total elapsed time including search, viewing, edits, confirmation and
review outside proposals. Re-viewing footage has a memory effect, so this is
descriptive and does not replace the 20-segment M2 pilot.

## Recovery

- `stale_snapshot`: refreeze after intentionally reviewing changed ROI/source/
  labels; never edit the old manifest.
- `out_of_memory`: keep the failed artifact, reduce selected footage or use the
  intended device; DINO has no silent CPU fallback.
- `deadline_exceeded` or interruption: verify failed status, ensure V1 is idle,
  then start a new run UUID.
- Quota/reserve failure: archive private old runs manually or free disk space.
  The tool never deletes video, labels, models or prior reports.
