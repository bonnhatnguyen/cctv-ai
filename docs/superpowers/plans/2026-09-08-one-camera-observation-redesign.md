# One-Camera Observation Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild live perception around tracked people, whole-body pose, generic visible-banknote candidates, and neutral hand-motion observations.

**Architecture:** RTSP-to-HLS remains unchanged. Ultralytics person tracking, MMPose RTMPose keypoints, and a local YOLO11 generic-banknote adapter publish normalized observations. A pure sixteen-frame rules module emits only observable hand-motion labels; FastAPI and React render those labels without transaction verdicts.

**Tech Stack:** Python 3.12, FastAPI, OpenCV, Ultralytics, ByteTrack, MMPose/RTMPose, React, TypeScript, Vite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-one-camera-observation-redesign.md`

## Global Constraints

- Support `cam-a` only; never emit a transaction, shortage, theft, review-required, or identity decision.
- Remove live YOLO-World prompt inference; it has no fallback role.
- Keep RTSP secrets outside source, responses, logs, and tests.
- Map every accepted Rokyuto/BanknotesRecognition source class to `banknote_candidate`, never a denomination; preserve AGPL-3.0 source information in `docs/model-sources.md`.
- Disable the banknote layer after ten clear-frame misses; use no money-model fallback.
- Every action needs eight consecutive valid frames in a sixteen-frame history. A missing required input emits `khong_du_quan_sat`.

---

### Task 1: Model layer contracts and source gate

**Files:**
- Create: `backend/app/vision/models.py`, `backend/tests/unit/test_models.py`, `docs/model-sources.md`
- Modify: `backend/app/config.py`, `backend/app/schemas.py`, `backend/pyproject.toml`

**Interfaces:**
- `ModelLayer(name: str, state: Literal["ready", "running", "model_unavailable", "inference_failed"], detail: str)`
- `ModelManifest(name: str, local_path: Path, source_url: str, license_name: str, class_map: Mapping[str, str])`

- [ ] **Step 1: Write the failing tests.**

```python
def test_missing_model_reports_neutral_health(tmp_path):
    manifest = ModelManifest("banknote", tmp_path / "missing.pt", "https://huggingface.co/Rokyuto/BanknotesRecognition", "AGPL-3.0", {})
    assert model_health(manifest).state == "model_unavailable"

def test_source_classes_never_become_denominations(tmp_path):
    manifest = ModelManifest("banknote", tmp_path / "model.pt", "source", "AGPL-3.0", {"5 BGN": "banknote_candidate"})
    assert manifest.class_map["5 BGN"] == "banknote_candidate"
```

- [ ] **Step 2: Run `cd backend && python -m pytest tests/unit/test_models.py -v`; verify FAIL because `vision.models` does not exist.**
- [ ] **Step 3: Implement immutable manifests, `model_health`, and settings `TRANSACTION_PERSON_MODEL_PATH`, `TRANSACTION_POSE_MODEL_PATH`, and `TRANSACTION_BANKNOTE_MODEL_PATH`. Each setting accepts a local path only.**
- [ ] **Step 4: Add documented MMPose dependencies and add the source URL, AGPL-3.0 notice, generic class rule, and ten-frame acceptance check to `docs/model-sources.md`.**
- [ ] **Step 5: Run the test; verify PASS; commit.**

```powershell
git add backend/app/config.py backend/app/schemas.py backend/app/vision/models.py backend/pyproject.toml backend/tests/unit/test_models.py docs/model-sources.md; git commit -m "feat: add model layer health contracts"
```

### Task 2: Person tracking and per-person whole-body pose

**Files:**
- Create: `backend/app/vision/person.py`, `backend/app/vision/pose.py`, `backend/tests/unit/test_person.py`, `backend/tests/unit/test_pose.py`
- Modify: `backend/app/vision/runtime.py`, `backend/app/vision/bytetrack_retail.yaml`

**Interfaces:**
- `PersonAdapter.update(frame: numpy.ndarray, timestamp_ms: int) -> list[Observation]`
- `PoseAdapter.detect(frame: numpy.ndarray, people: Sequence[Observation]) -> list[PoseKeypoints]`
- `PoseKeypoints(person_track_id: str, left_wrist: Point | None, right_wrist: Point | None, left_hip: Point | None, right_hip: Point | None)`

- [ ] **Step 1: Write failing person/pose tests.**

```python
def test_person_tracks_are_namespaced_and_non_people_are_dropped():
    output = adapter_from_rows([("person", .9, (10, 10, 30, 30), 4), ("chair", .9, (1, 1, 2, 2), 7)]).update(frame, 1000)
    assert [(x.kind, x.track_id) for x in output] == [("person", "cam-a-person-4")]

def test_pose_below_visibility_threshold_is_absent():
    pose = pose_adapter_from_keypoints([(0.2, 0.2, .54)]).detect(frame, [person("cam-a-person-4")])[0]
    assert pose.left_wrist is None
```

- [ ] **Step 2: Run `cd backend && python -m pytest tests/unit/test_person.py tests/unit/test_pose.py -v`; verify FAIL.**
- [ ] **Step 3: Implement person-only Ultralytics/ByteTrack inference with class-aware NMS. Implement RTMPose crop inference for each person, conversion to normalized frame coordinates, and a `0.55` confidence floor. A missing pose model degrades only the pose layer.**
- [ ] **Step 4: Remove YOLO-World prompt classes and the old MediaPipe hand runtime path.**
- [ ] **Step 5: Run tests; verify PASS; commit.**

```powershell
git add backend/app/vision/person.py backend/app/vision/pose.py backend/app/vision/runtime.py backend/app/vision/bytetrack_retail.yaml backend/tests/unit/test_person.py backend/tests/unit/test_pose.py; git commit -m "feat: add person tracking and whole-body pose"
```

### Task 3: Generic banknote candidates and temporal action rules

**Files:**
- Create: `backend/app/vision/banknote.py`, `backend/app/vision/actions.py`, `backend/tests/unit/test_banknote.py`, `backend/tests/unit/test_actions.py`
- Modify: `backend/app/vision/runtime.py`, `.gitignore`, `backend/app/schemas.py`

**Interfaces:**
- `BanknoteAdapter.update(frame: numpy.ndarray, timestamp_ms: int) -> list[Observation]`
- `TemporalActionObserver.update(timestamp_ms: int, people: Sequence[Observation], poses: Sequence[PoseKeypoints], notes: Sequence[Observation]) -> list[ActionObservation]`

- [ ] **Step 1: Write failing detector/action tests.**

```python
def test_banknote_source_class_is_generic_and_trackable():
    row = banknote_adapter_from_rows([("5 BGN", .9, (1, 1, 10, 10), 3)]).update(frame, 1000)[0]
    assert (row.kind, row.denomination, row.track_id) == ("banknote_candidate", None, "cam-a-banknote-3")

def test_customer_directed_wrist_motion_needs_eight_frames():
    observer = TemporalActionObserver()
    for timestamp in range(7):
        assert observer.update(timestamp, two_people(), wrist_toward_customer(), []) == []
    assert observer.update(7, two_people(), wrist_toward_customer(), [])[0].kind == "tay_huong_khach"
```

- [ ] **Step 2: Run `cd backend && python -m pytest tests/unit/test_banknote.py tests/unit/test_actions.py -v`; verify FAIL.**
- [ ] **Step 3: Implement a separate local YOLO11/ByteTrack adapter. Accept only declared source classes, map them to `banknote_candidate`, and disable after ten clear-frame misses. Do not call a network API in inference.**
- [ ] **Step 4: Implement sixteen-frame deques keyed by person track. Add exact geometry rules: wrist moving toward another person box (`tay_huong_khach`), wrist near own hip (`tay_vao_vung_tui`), note centre near wrist (`tien_di_cung_tay`), and prior note loss while wrist remains near hip (`tien_mat_dau_gan_vung_tui`). Missing person, pose, or note clears the sequence with `khong_du_quan_sat`.**
- [ ] **Step 5: Run tests; verify PASS; commit.**

```powershell
git add backend/app/vision/banknote.py backend/app/vision/actions.py backend/app/vision/runtime.py backend/app/schemas.py backend/tests/unit/test_banknote.py backend/tests/unit/test_actions.py .gitignore docs/model-sources.md; git commit -m "feat: derive neutral hand-motion observations"
```

### Task 4: Neutral API/UI and end-to-end verification

**Files:**
- Modify: `backend/app/api.py`, `backend/app/vision/runtime.py`, `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/LiveCamera.tsx`, `frontend/src/styles.css`
- Create: `backend/tests/integration/test_inference_status.py`, `frontend/src/App.test.tsx`

**Interfaces:**
- `GET /api/inference/status` returns `layers`, `detections`, and `actions` for `cam-a` only.
- It never returns transaction statuses, review cases, or a denomination.

- [ ] **Step 1: Write failing boundary tests.**

```python
def test_status_has_observations_not_transaction_verdicts(client):
    payload = client.get("/api/inference/status").json()["cam-a"]
    assert "layers" in payload and "actions" in payload
    assert "transaction_statuses" not in payload
    assert "review_required" not in str(payload)
```

```tsx
it("uses a neutral pocket-zone label", async () => {
  render(<App />);
  expect(await screen.findByText("tay vào vùng túi")).toBeVisible();
  expect(screen.queryByText(/đút tiền/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run `cd backend && python -m pytest tests/integration/test_inference_status.py -v` and `cd frontend && pnpm test -- --run`; verify FAIL.**
- [ ] **Step 3: Render layer-health cards, action timeline, and HLS-aligned overlay labels. Remove prompt-model counts, transaction-chain text, and review queue. Render unavailable cash as `model chưa sẵn sàng`, with no fake boxes.**
- [ ] **Step 4: Run full verification.**

```powershell
cd backend; python -m pytest -q; cd ..\frontend; pnpm test -- --run; pnpm build
```

- [ ] **Step 5: Check ten clear camera frames before marking the banknote layer running; verify only neutral labels appear; commit.**

```powershell
git add backend/app/api.py backend/app/vision/runtime.py backend/tests/integration/test_inference_status.py frontend/src/api.ts frontend/src/App.tsx frontend/src/LiveCamera.tsx frontend/src/styles.css frontend/src/App.test.tsx; git commit -m "feat: show neutral one-camera action observations"
```

## Plan Self-Review

- Tasks 1–3 cover model boundaries, tracking, pose, generic money candidates, and every approved action rule.
- Task 4 enforces the one-camera safety boundary in both API and UI.
- Every task has failing tests, passing tests, and a commit step; all later interfaces are defined by earlier tasks.
