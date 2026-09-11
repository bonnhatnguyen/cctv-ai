# V2 M2 Action Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, frame-exact workflow that creates, edits, deletes, restores, and reviews the five approved basket action labels on the selected V2 clip.

**Architecture:** Extend the annotation SQLite database through migration v2 and keep typed Python contracts as the source for generated TypeScript. Every action binds to the selected clip and immutable ROI revision. The existing exact-frame viewer owns playback and frame selection; focused interaction, event timeline, and review components consume it without duplicating media state.

**Tech Stack:** Python 3.12, SQLite, FastAPI, Pydantic v2, React, TypeScript, Vitest, local Chrome.

**Spec:** `docs/superpowers/specs/2026-09-10-v2-basket-action-annotation-design.md`

## Global Constraints

- Labels are exactly `hand_in`, `hand_out`, `take_out`, `put_in`, `unclear`; UI may visually prioritize the first two and `unclear` but contracts support all five.
- Source truth is zero-based inclusive `start_frame` and `end_frame`. `crossing_frame` is required only for `hand_in` and `hand_out`, and must be inside the interval.
- A clear action belongs to one clip-local interaction with `hand=left|right|unknown`; a ROI-scoped `unclear` may have no interaction.
- `unclear` carries a non-empty subset of uncertain labels plus one approved reason. It is neither a positive action nor a negative sample.
- Every event binds the current immutable ROI revision and `guideline_version=1`; changing ROI marks current events `needs_review` and invalidates coverage.
- Mutations use operation UUID replay, expected clip revision, immutable revision rows, and HTTP 409 on stale writes while retaining the browser draft.
- Deletion is a tombstone and can be restored. Confirming one event never creates negative supervision; only explicit per-label review coverage can do that later in M3 export.
- All media, labels, SQLite files, and acceptance artifacts remain local and Git-ignored. APIs accept UUIDs, never arbitrary filesystem paths.
- Existing M1 exact-frame, SAR, privacy, release/reprepare, V1 tracking, and clip binding behavior remains intact.

---

### Task 1: Migration v2 and typed action domain

**Files:**
- Modify: `backend/app/annotation/database.py`
- Modify: `backend/app/annotation/contracts.py`
- Modify: `backend/app/annotation/repository.py`
- Modify: `backend/tests/annotation/test_contracts.py`
- Modify: `backend/tests/annotation/test_repository.py`
- Modify: `backend/tests/annotation/test_database.py`

**Interfaces:**
- Produces `InteractionView`, `ActionAnnotationView`, `ActionWorkspaceView`, write/review/tombstone requests, and repository methods consumed by Task 2.
- Preserves all v1 rows and creates a SQLite backup before upgrading a non-empty v1 database.

- [ ] Write RED contract tests for each label rule, interval/crossing validation, unclear fields, and forbidden extra fields.
- [ ] Run focused tests and confirm failures are caused by absent M2 contracts.
- [ ] Add migration v2 tables for interactions, action annotations, immutable action revisions, review coverage, and indexes/FKs; migrate with the existing rollback-safe transaction loop.
- [ ] Add repository read workspace plus create/update/delete/restore/confirm event and create/update interaction methods. Each mutation replays before revision checks, advances clip revision atomically, records a revision, and validates clip/ROI/frame/interaction ownership.
- [ ] In `save_roi`, mark live annotations `needs_review` and invalidate active review coverage in the same transaction when the ROI revision changes.
- [ ] Test reopened DB preservation, rollback injection, FK enforcement, concurrent stale writers, lost-response replay, cross-clip interaction rejection, overlap preservation, tombstone restore, and ROI invalidation.
- [ ] Run all annotation repository/database/contract tests and commit `feat(v2): add versioned action annotation domain`.

### Task 2: Action and review HTTP API plus generated contracts

**Files:**
- Modify: `backend/app/annotation/api.py`
- Modify: `backend/tests/annotation/test_api.py`
- Modify: `scripts/generate-annotation-types.py`
- Regenerate: `frontend/src/annotation/types.generated.ts`
- Regenerate: `frontend/src/annotation/schema.generated.json`
- Modify: `frontend/src/annotation/api.ts`

**Interfaces:**
- Produces GET workspace and mutation functions used by Task 3.
- Error mapping remains path-free and uses `annotation_not_found`, `annotation_conflict`, `invalid_annotation_request`, and `source_unavailable`.

- [ ] Write RED API tests for workspace load and every mutation, including malformed frames, label semantics, stale revision, idempotent retry, and no path leakage.
- [ ] Add clip-scoped routes for workspace, interactions, events, event lifecycle, confirmation, and review coverage. Return the latest `ActionWorkspaceView` after mutations so UI state and clip revision move together.
- [ ] Extend the offline generator mapping for literals, optionals, arrays, and the new DTOs; regenerate outputs and verify drift detection.
- [ ] Add structured frontend API calls with AbortSignal and stable operation IDs supplied by the editor.
- [ ] Run annotation API tests, generator check, TypeScript build, and commit `feat(v2): expose action annotation API`.

### Task 3: Ordered annotation workspace, event editor, and timeline

**Files:**
- Create: `frontend/src/annotation/ActionWorkspace.tsx`
- Create: `frontend/src/annotation/ActionEditor.tsx`
- Create: `frontend/src/annotation/ActionTimeline.tsx`
- Create: `frontend/src/annotation/actionRules.ts`
- Create tests beside each component.
- Modify: `frontend/src/annotation/FrameViewer.tsx`
- Modify: `frontend/src/annotation/Workspace.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes Task 2 `ActionWorkspaceView` API and the existing exact-frame `index`, `frameReady`, playback, ROI, and SAR geometry.
- Produces a complete clip-local workflow: ROI confirmation then annotate then review.

- [ ] Write RED tests for mode order `Vùng rổ → Gán nhãn → Kiểm tra`, direct entry to annotation when ROI exists, disabled frame capture until exact image load, keyboard focus isolation, and unsaved draft warnings.
- [ ] Add a compact mode header. Keep clip list and storage secondary; hide ROI setup controls and tracking detail while annotating.
- [ ] Implement interaction selection/creation with one anatomical hand value. Person-track reference stays optional and absent by default.
- [ ] Implement frame capture buttons and shortcuts: I start, C crossing, O end, 1–5 labels, Ctrl+S save, Escape cancel. For `take_out`, `put_in`, and `unclear`, crossing remains null.
- [ ] Add label-dependent hand/object/visibility/unclear controls and client validation mirroring the contract only for immediate feedback; server remains authoritative.
- [ ] Render multi-row interval bars without merging overlaps. Selecting a bar seeks its start and loads it for edit; offer confirm, delete, and restore with visible saved/review state.
- [ ] Keep a failed/stale save draft intact and retry the same operation UUID only for the unchanged payload.
- [ ] Run focused UI tests, full frontend tests/build, and commit `feat(v2): add action labeling timeline`.

### Task 4: Review coverage, real workflow, and M2 pilot gate

**Files:**
- Create: `frontend/src/annotation/ReviewPanel.tsx`
- Create: `frontend/src/annotation/ReviewPanel.test.tsx`
- Create: `scripts/verify-v2-m2-pilot.py`
- Create: `backend/tests/annotation/test_m2_verification.py`
- Modify: `docs/v2-user-guide.md`
- Modify: `docs/v2-acceptance.md`

**Interfaces:**
- Consumes confirmed events and explicit coverage records.
- Produces local pilot evidence; it does not export training data or claim model accuracy.

- [ ] Write RED tests that review coverage is per label and frame interval, requires explicit operator confirmation, and is invalidated only over affected ranges/classes after event changes.
- [ ] Implement ReviewPanel for confirming events and recording complete coverage per chosen labels. Display unreviewed intervals as unknown rather than background.
- [ ] Implement a deterministic local pilot manifest and verifier for 20 preselected shop segments: at least two examples per clear action, two unclear, and two reviewed no-action segments. If footage lacks a class, record `PENDING_DATA` rather than relabeling another action.
- [ ] Record first-pass annotations and support a blinded shuffled second pass after 24 hours. Compute label-set agreement, one-to-one maximum temporal-IoU matching, and crossing-frame error using the exact thresholds in the spec.
- [ ] Verify the full real-browser flow: imported clip without tracking → existing ROI → create all available labels → reload/restart → edit/delete/restore → confirm/coverage → two-tab conflict. Check SAR 2:1 and ROI binding.
- [ ] Run contract drift, compile, full backend tests, full frontend tests/build, and `git diff --check`; update the ledger with current counts and limitations.
- [ ] Review the whole M2 diff for spec compliance and privacy, then commit `feat(v2): complete manual basket action labeling` only when mandatory software gates pass. Pilot data availability may remain explicitly pending and does not convert unknown footage into labels.

## Plan self-review

- Tasks form a dependency chain: domain → API → UI → review/pilot, with one source of contracts and one clip revision stream.
- All M2 spec fields have an owning task. M3 export, split registry, portable bundles, training adapters, auto-label, pose, and model training are excluded.
- The ordered UI addresses the current mixed workspace without a second media/player state or a temporary three-label schema.
- No placeholders or unowned interfaces remain; every mutation and lifecycle transition has a RED/GREEN gate.
