import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnnotationApiError } from "./api";
import { ActionWorkspace } from "./ActionWorkspace";
import type { ActionWorkspaceView, ClipView } from "./types.generated";

const mocks = vi.hoisted(() => ({
  get: vi.fn(), getClip: vi.fn(), create: vi.fn(), interaction: vi.fn(), update: vi.fn(),
  confirm: vi.fn(), remove: vi.fn(), restore: vi.fn(), coverage: vi.fn(), nextOperation: 0,
  assistanceModels: vi.fn(), assistanceRuns: vi.fn(), assistanceRun: vi.fn(),
  assistanceSuggestions: vi.fn(), assistanceStart: vi.fn(), assistanceCancel: vi.fn(),
  assistanceReject: vi.fn(),
}));
vi.mock("./api", () => ({
  AnnotationApiError: class extends Error {
    constructor(public status: number, public code: string, public conflictingAnnotationId: string | null = null) { super(code); }
  },
  newOperationId: () => `operation-${++mocks.nextOperation}`,
  getClip: mocks.getClip,
  getActionWorkspace: mocks.get,
  createAction: mocks.create,
  createInteraction: mocks.interaction,
  createReviewCoverage: mocks.coverage,
  updateAction: mocks.update,
  confirmAction: mocks.confirm,
  deleteAction: mocks.remove,
  restoreAction: mocks.restore,
  listAssistanceModels: mocks.assistanceModels,
  listAssistanceRuns: mocks.assistanceRuns,
  getAssistanceRun: mocks.assistanceRun,
  listAssistanceSuggestions: mocks.assistanceSuggestions,
  startAssistanceRun: mocks.assistanceStart,
  cancelAssistanceRun: mocks.assistanceCancel,
  rejectAssistanceSuggestion: mocks.assistanceReject,
}));

const clip: ClipView = {
  schema_version: 1, id: "clip", source_job_id: "job", original_name: "shop.mp4",
  revision: 4, preparation_state: "ready", source_state: "available", failure_code: null,
  source_sha256: "hash", media: { frame_count: 20, fps_num: 25, fps_den: 1, width: 160, height: 90, sample_aspect_ratio: "1:1" },
  roi: { id: "roi", revision: 1, camera_setup_id: "setup", polygon: [{ x: .1, y: .1 }, { x: .8, y: .1 }, { x: .8, y: .8 }], template_revision_id: null },
  preview_url: "/preview", prepared_bytes: 1,
};
const workspace: ActionWorkspaceView = {
  clip_id: "clip", clip_revision: 4, roi_revision_id: "roi",
  interactions: [{ id: "interaction", clip_id: "clip", revision: 1, hand: "right", tracking_job_id: null, local_track_id: null }],
  annotations: [], review_coverage: [],
};

beforeEach(() => {
  mocks.nextOperation = 0;
  mocks.get.mockReset().mockResolvedValue(workspace);
  mocks.getClip.mockReset().mockResolvedValue(clip);
  mocks.create.mockReset().mockResolvedValue({ ...workspace, clip_revision: 5 });
  mocks.interaction.mockReset();
  mocks.update.mockReset();
  mocks.confirm.mockReset();
  mocks.remove.mockReset();
  mocks.restore.mockReset();
  mocks.coverage.mockReset();
  mocks.assistanceModels.mockReset().mockResolvedValue([
    { model: "dino", available: true, device: "cuda:0", error_code: null },
  ]);
  mocks.assistanceRuns.mockReset().mockResolvedValue({ items: [{
    id: "run", clip_id: "clip", model: "dino", status: "succeeded",
    start_frame: 0, end_frame: 19, processed_frames: 4, scheduled_frames: 4,
    error_code: null, source_sha256: "a".repeat(64), roi_revision_id: "roi",
    guideline_version: 1, device: "cuda:0", config_sha256: null, asset_sha256: null,
    created_at: "2026-09-13T00:00:00Z", updated_at: "2026-09-13T00:01:00Z",
  }], next_cursor: null });
  mocks.assistanceSuggestions.mockReset().mockResolvedValue({ items: [{
    id: "suggestion", run_id: "run", clip_id: "clip", proposal_key: "p1",
    label: "hand_in", action_start_frame: 5, action_end_frame: 9,
    view_start_frame: 4, view_end_frame: 10, crossing_estimate: 7,
    crossing_bracket_start: 6, crossing_bracket_end: 8, reason: "crossing",
    review_state: "pending", accepted_annotation_id: null,
  }], next_cursor: null });
  mocks.assistanceRun.mockReset();
  mocks.assistanceStart.mockReset();
  mocks.assistanceCancel.mockReset();
  mocks.assistanceReject.mockReset();
});
afterEach(cleanup);

it("saves hand_in from exact I/C/O shortcuts with a caller-owned operation id", async () => {
  render(<ActionWorkspace clip={clip} index={7} onIndex={vi.fn()} frameReady
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  await screen.findByText("Lượt 1");
  await screen.findByRole("button", { name: /Bắt đầu —/ });
  fireEvent.keyDown(window, { key: "i" });
  fireEvent.keyDown(window, { key: "c" });
  fireEvent.keyDown(window, { key: "o" });
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(
    "clip",
    expect.objectContaining({
      operation_id: "operation-1", label: "hand_in", interaction_id: "interaction",
      start_frame: 7, crossing_frame: 7, end_frame: 7,
    }),
  ));
});

it("reviews a model proposal through the normal draft save path", async () => {
  const seek = vi.fn();
  render(<ActionWorkspace clip={clip} index={7} onIndex={seek} frameReady
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Dùng làm nháp" }));
  expect(screen.getByText(/Mốc do model gợi ý/i)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /Lượt 1/ }));
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));

  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith("clip", expect.objectContaining({
    suggestion_id: "suggestion", label: "hand_in", interaction_id: "interaction",
    start_frame: 5, crossing_frame: 7, end_frame: 9,
  })));
  expect(mocks.confirm).not.toHaveBeenCalled();
  expect(mocks.coverage).not.toHaveBeenCalled();
});

it("keeps the draft and reuses its operation id after a lost response", async () => {
  mocks.create.mockRejectedValueOnce(new Error("connection lost"))
    .mockResolvedValueOnce({ ...workspace, clip_revision: 5 });
  const dirty = vi.fn();
  render(<ActionWorkspace clip={clip} index={9} onIndex={vi.fn()} frameReady
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={dirty} />);
  await screen.findByText("Lượt 1");
  await screen.findByRole("button", { name: /Bắt đầu —/ });
  (document.activeElement as HTMLElement)?.blur();
  fireEvent.keyDown(window, { key: "i" });
  fireEvent.keyDown(window, { key: "c" });
  fireEvent.keyDown(window, { key: "o" });
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/Nháp vẫn được giữ/i);
  expect(screen.getByRole("button", { name: /Bắt đầu 9/ })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(2));
  const first = mocks.create.mock.calls[0][1];
  const second = mocks.create.mock.calls[1][1];
  expect(second.operation_id).toBe(first.operation_id);
  expect(dirty).toHaveBeenCalledWith(true);
});

it("reloads a stale server revision and preserves the draft for an explicit retry", async () => {
  const latest = { ...workspace, clip_revision: 8 };
  mocks.get.mockResolvedValueOnce(workspace).mockResolvedValueOnce(latest);
  mocks.create.mockRejectedValueOnce(new AnnotationApiError(409, "annotation_conflict"))
    .mockResolvedValueOnce({ ...latest, clip_revision: 9 });
  render(<ActionWorkspace clip={clip} index={6} onIndex={vi.fn()} frameReady
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  await screen.findByText("Lượt 1");
  fireEvent.keyDown(window, { key: "i" });
  fireEvent.keyDown(window, { key: "c" });
  fireEvent.keyDown(window, { key: "o" });
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/Đã tải bản mới từ server.*Nháp vẫn được giữ/i);
  expect(screen.getByRole("button", { name: /Bắt đầu 6/ })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(2));
  expect(mocks.create.mock.calls[1][1]).toEqual(expect.objectContaining({
    expected_clip_revision: 8,
    start_frame: 6,
    crossing_frame: 6,
    end_frame: 6,
  }));
  expect(mocks.create.mock.calls[1][1].operation_id)
    .not.toBe(mocks.create.mock.calls[0][1].operation_id);
});

it("reloads the visible ROI and requires exact frames to be marked again after an ROI conflict", async () => {
  const changedClip = {
    ...clip,
    revision: 8,
    roi: { ...clip.roi!, id: "roi-2", revision: 2, polygon: [{ x: .2, y: .2 }, { x: .7, y: .2 }, { x: .7, y: .7 }] },
  };
  mocks.get.mockResolvedValueOnce(workspace).mockResolvedValueOnce({
    ...workspace,
    clip_revision: 8,
    roi_revision_id: "roi-2",
  });
  mocks.getClip.mockResolvedValue(changedClip);
  mocks.create.mockRejectedValueOnce(new AnnotationApiError(409, "annotation_conflict"));
  const reloadClip = vi.fn();
  render(<ActionWorkspace clip={clip} index={6} onIndex={vi.fn()} frameReady
    onClipRevision={vi.fn()} onClipReload={reloadClip} onDirtyChange={vi.fn()} />);
  await screen.findByText("Lượt 1");
  fireEvent.keyDown(window, { key: "i" });
  fireEvent.keyDown(window, { key: "c" });
  fireEvent.keyDown(window, { key: "o" });
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/ROI đã thay đổi.*đánh dấu lại/i);
  expect(mocks.getClip).toHaveBeenCalledWith("clip");
  expect(reloadClip).toHaveBeenCalledWith(changedClip);
  expect(screen.getByRole("button", { name: /Bắt đầu —/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /Qua biên —/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /Kết thúc —/ })).toBeVisible();
});

it("adopts a newer ROI before allowing edits on the initial workspace load", async () => {
  const changedClip = {
    ...clip,
    revision: 8,
    roi: { ...clip.roi!, id: "roi-2", revision: 2, polygon: [{ x: .2, y: .2 }, { x: .7, y: .2 }, { x: .7, y: .7 }] },
  };
  mocks.get.mockResolvedValue({ ...workspace, clip_revision: 8, roi_revision_id: "roi-2" });
  mocks.getClip.mockResolvedValue(changedClip);
  const reloadClip = vi.fn();
  render(<ActionWorkspace clip={clip} index={0} onIndex={vi.fn()} frameReady
    onClipRevision={vi.fn()} onClipReload={reloadClip} onDirtyChange={vi.fn()} />);

  await screen.findByText("Lượt 1");
  expect(mocks.getClip).toHaveBeenCalledWith("clip");
  expect(reloadClip).toHaveBeenCalledWith(changedClip);
});

it("drops a late workspace response from the previously selected clip", async () => {
  let resolveA!: (value: ActionWorkspaceView) => void;
  let resolveB!: (value: ActionWorkspaceView) => void;
  mocks.get.mockImplementation((clipId: string) => new Promise((resolve) => {
    if (clipId === "clip") resolveA = resolve;
    else resolveB = resolve;
  }));
  const secondClip = {
    ...clip, id: "clip-b", source_job_id: "job-b",
    roi: { ...clip.roi!, id: "roi-b" },
  };
  const { rerender } = render(<ActionWorkspace clip={clip} index={0} onIndex={vi.fn()}
    frameReady onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  rerender(<ActionWorkspace clip={secondClip} index={0} onIndex={vi.fn()}
    frameReady onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  resolveA(workspace);
  await Promise.resolve();
  expect(screen.queryByText("Lượt 1")).not.toBeInTheDocument();
  resolveB({ ...workspace, clip_id: "clip-b", roi_revision_id: "roi-b", interactions: [] });
  expect(await screen.findByText(/không rõ lượt/i)).toBeVisible();
  expect(screen.queryByText("Lượt 1")).not.toBeInTheDocument();
});

it("allows unclear to be scoped to the whole ROI without an interaction", async () => {
  render(<ActionWorkspace clip={clip} index={11} onIndex={vi.fn()} frameReady
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  await screen.findByText("Lượt 1");
  fireEvent.click(screen.getByRole("button", { name: /Toàn ROI/ }));
  fireEvent.click(screen.getByRole("button", { name: /5\s*Unclear/ }));
  fireEvent.keyDown(window, { key: "i" });
  fireEvent.keyDown(window, { key: "o" });
  fireEvent.change(screen.getByLabelText("Lý do"), { target: { value: "occlusion" } });
  fireEvent.click(screen.getByRole("button", { name: "Lưu nhãn" }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(
    "clip",
    expect.objectContaining({
      label: "unclear", interaction_id: null, crossing_frame: null,
      uncertain_labels: ["hand_in", "hand_out", "take_out", "put_in"],
    }),
  ));
});

it("asks before replacing an unsaved draft with a saved event", async () => {
  const event = {
    id: "event", clip_id: "clip", roi_revision_id: "roi", interaction_id: "interaction",
    revision: 1, label: "put_in" as const, start_frame: 2, end_frame: 3,
    crossing_frame: null, object_kind: "cash" as const, visibility: "clear" as const,
    uncertain_labels: [], unclear_reason: null, review_state: "draft" as const,
    guideline_version: 1 as const, deleted: false,
  };
  mocks.get.mockResolvedValue({ ...workspace, annotations: [event] });
  const seek = vi.fn();
  render(<ActionWorkspace clip={clip} index={7} onIndex={seek} frameReady
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  await screen.findByText("Lượt 1");
  (document.activeElement as HTMLElement)?.blur();
  fireEvent.keyDown(window, { key: "i" });
  fireEvent.click(screen.getByRole("button", { name: /Put in\s*2–3/ }));
  expect(await screen.findByRole("heading", { name: /chưa lưu/i })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Hủy bỏ" }));
  expect(seek).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: "Nhãn mới" })).toBeVisible();
});

it("reuses a lifecycle operation id after a lost response", async () => {
  const event = {
    id: "event", clip_id: "clip", roi_revision_id: "roi", interaction_id: "interaction",
    revision: 1, label: "take_out" as const, start_frame: 2, end_frame: 3,
    crossing_frame: null, object_kind: "cash" as const, visibility: "clear" as const,
    uncertain_labels: [], unclear_reason: null, review_state: "draft" as const,
    guideline_version: 1 as const, deleted: false,
  };
  const next = { ...workspace, clip_revision: 5, annotations: [{ ...event, revision: 2, deleted: true }] };
  mocks.get.mockResolvedValue({ ...workspace, annotations: [event] });
  mocks.remove.mockRejectedValueOnce(new Error("lost")).mockResolvedValueOnce(next);
  render(<ActionWorkspace clip={clip} index={0} onIndex={vi.fn()} frameReady
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  await screen.findByRole("button", { name: "Xóa" });
  fireEvent.click(screen.getByRole("button", { name: "Xóa" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/thử lại/i);
  fireEvent.click(screen.getByRole("button", { name: "Xóa" }));
  await waitFor(() => expect(mocks.remove).toHaveBeenCalledTimes(2));
  expect(mocks.remove.mock.calls[1][2].operation_id).toBe(
    mocks.remove.mock.calls[0][2].operation_id,
  );
});

it("records explicit review coverage and reuses its operation id after a lost response", async () => {
  const next = {
    ...workspace,
    clip_revision: 5,
    review_coverage: [{
      id: "coverage", clip_id: "clip", roi_revision_id: "roi", revision: 1,
      start_frame: 4, end_frame: 4, reviewed_labels: ["hand_in" as const],
      guideline_version: 1 as const, active: true,
    }],
  };
  mocks.coverage.mockRejectedValueOnce(new Error("lost")).mockResolvedValueOnce(next);
  render(<ActionWorkspace clip={clip} index={4} onIndex={vi.fn()} frameReady reviewOnly
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  await screen.findByText("Phạm vi đã kiểm tra");
  fireEvent.click(screen.getByRole("button", { name: /Đặt bắt đầu/ }));
  fireEvent.click(screen.getByRole("button", { name: /Đặt kết thúc/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Hand in" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Tôi đã xem toàn bộ/ }));
  fireEvent.click(screen.getByRole("button", { name: "Ghi nhận đã kiểm tra" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/thử lại/i);
  fireEvent.click(screen.getByRole("button", { name: "Ghi nhận đã kiểm tra" }));
  await waitFor(() => expect(mocks.coverage).toHaveBeenCalledTimes(2));
  expect(mocks.coverage.mock.calls[0][1]).toEqual(expect.objectContaining({
    start_frame: 4, end_frame: 4, reviewed_labels: ["hand_in"],
  }));
  expect(mocks.coverage.mock.calls[1][1].operation_id)
    .toBe(mocks.coverage.mock.calls[0][1].operation_id);
});

it("clears review frame attestation when a lifecycle conflict adopts a newer ROI", async () => {
  const event = {
    id: "event", clip_id: "clip", roi_revision_id: "roi", interaction_id: "interaction",
    revision: 1, label: "take_out" as const, start_frame: 2, end_frame: 3,
    crossing_frame: null, object_kind: "cash" as const, visibility: "clear" as const,
    uncertain_labels: [], unclear_reason: null, review_state: "draft" as const,
    guideline_version: 1 as const, deleted: false,
  };
  const changedClip = {
    ...clip,
    revision: 8,
    roi: { ...clip.roi!, id: "roi-2", revision: 2, polygon: [{ x: .2, y: .2 }, { x: .7, y: .2 }, { x: .7, y: .7 }] },
  };
  mocks.get.mockResolvedValueOnce({ ...workspace, annotations: [event] })
    .mockResolvedValueOnce({ ...workspace, clip_revision: 8, roi_revision_id: "roi-2", annotations: [{ ...event, roi_revision_id: "roi-2", revision: 2, review_state: "needs_review" }] });
  mocks.getClip.mockResolvedValue(changedClip);
  mocks.remove.mockRejectedValueOnce(new AnnotationApiError(409, "annotation_conflict"));
  render(<ActionWorkspace clip={clip} index={4} onIndex={vi.fn()} frameReady reviewOnly
    onClipRevision={vi.fn()} onClipReload={vi.fn()} onDirtyChange={vi.fn()} />);
  await screen.findByText("Phạm vi đã kiểm tra");
  fireEvent.click(screen.getByRole("button", { name: /Đặt bắt đầu/ }));
  fireEvent.click(screen.getByRole("button", { name: /Đặt kết thúc/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Hand in" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Tôi đã xem toàn bộ/ }));
  fireEvent.click(screen.getByRole("button", { name: "Xóa" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/ROI đã thay đổi/i);
  expect(screen.getByRole("button", { name: /Đặt bắt đầu —/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /Đặt kết thúc —/ })).toBeVisible();
  expect(screen.getByRole("checkbox", { name: /Tôi đã xem toàn bộ/ })).not.toBeChecked();
});
