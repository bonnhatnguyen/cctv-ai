import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AssistanceRunView, AssistanceSuggestionView } from "./types.generated";

const mocks = vi.hoisted(() => ({
  models: vi.fn(), runs: vi.fn(), run: vi.fn(), suggestions: vi.fn(),
  start: vi.fn(), cancel: vi.fn(), reject: vi.fn(), operation: 0,
}));

vi.mock("./api", () => ({
  newOperationId: () => `operation-${++mocks.operation}`,
  listAssistanceModels: mocks.models,
  listAssistanceRuns: mocks.runs,
  getAssistanceRun: mocks.run,
  listAssistanceSuggestions: mocks.suggestions,
  startAssistanceRun: mocks.start,
  cancelAssistanceRun: mocks.cancel,
  rejectAssistanceSuggestion: mocks.reject,
}));

import { AssistedReviewPanel } from "./AssistedReviewPanel";

const completedRun: AssistanceRunView = {
  id: "run-1", clip_id: "clip", model: "dino", status: "succeeded",
  start_frame: 100, end_frame: 170, processed_frames: 15, scheduled_frames: 15,
  error_code: null, source_sha256: "a".repeat(64), roi_revision_id: "roi",
  guideline_version: 1, device: "cuda:0", config_sha256: null, asset_sha256: null,
  created_at: "2026-09-13T00:00:00Z", updated_at: "2026-09-13T00:01:00Z",
};
const suggestion: AssistanceSuggestionView = {
  id: "suggestion-1", run_id: "run-1", clip_id: "clip", proposal_key: "p1",
  label: "hand_in", action_start_frame: 122, action_end_frame: 141,
  view_start_frame: 120, view_end_frame: 145, crossing_estimate: 132,
  crossing_bracket_start: 130, crossing_bracket_end: 134, reason: "crossing",
  review_state: "pending", accepted_annotation_id: null,
};

beforeEach(() => {
  mocks.operation = 0;
  mocks.models.mockReset().mockResolvedValue([
    { model: "dino", available: true, device: "cuda:0", error_code: null },
    { model: "mediapipe", available: true, device: "cpu", error_code: null },
  ]);
  mocks.runs.mockReset().mockResolvedValue({ items: [completedRun], next_cursor: null });
  mocks.run.mockReset().mockResolvedValue(completedRun);
  mocks.suggestions.mockReset().mockResolvedValue({ items: [suggestion], next_cursor: null });
  mocks.start.mockReset().mockResolvedValue({ ...completedRun, id: "run-2", status: "queued" });
  mocks.cancel.mockReset();
  mocks.reject.mockReset().mockResolvedValue({ ...suggestion, review_state: "rejected" });
});
afterEach(cleanup);

it("uses a model suggestion as an estimated draft without saving it", async () => {
  const seek = vi.fn();
  const use = vi.fn();
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={110} onSeek={seek} onUseSuggestion={use} onQueueChanged={vi.fn()} />);

  fireEvent.click(await screen.findByRole("button", { name: /Đoạn 120–145/ }));
  expect(seek).toHaveBeenCalledWith(120);
  fireEvent.click(screen.getByRole("button", { name: "Dùng làm nháp" }));
  expect(use).toHaveBeenCalledWith(expect.objectContaining({
    id: "suggestion-1", crossing_estimate: 132,
  }));
});

it("keeps an empty model result distinct from reviewed coverage", async () => {
  mocks.suggestions.mockResolvedValue({ items: [], next_cursor: null });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={110} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  expect(await screen.findByText(/Model không tìm thấy gợi ý/i)).toBeVisible();
  expect(screen.getByText(/vẫn phải xem phần còn lại/i)).toBeVisible();
});

it("starts DINO on an explicit inclusive frame range", async () => {
  mocks.runs.mockResolvedValue({ items: [], next_cursor: null });
  mocks.suggestions.mockResolvedValue({ items: [], next_cursor: null });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);
  await screen.findByRole("button", { name: "Chạy model" });
  fireEvent.change(screen.getByLabelText("Frame bắt đầu hỗ trợ"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Frame kết thúc hỗ trợ"), { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: "Chạy model" }));

  await waitFor(() => expect(mocks.start).toHaveBeenCalledWith("clip", {
    operation_id: "operation-1", expected_clip_revision: 4,
    model: "dino", start_frame: 10, end_frame: 80,
  }));
});

it("shows the latest failed run instead of treating it as an empty result", async () => {
  mocks.runs.mockResolvedValue({ items: [{
    ...completedRun, status: "failed", error_code: "out_of_memory",
  }], next_cursor: null });
  mocks.suggestions.mockResolvedValue({ items: [], next_cursor: null });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  expect(await screen.findByRole("alert")).toHaveTextContent(/Model thất bại.*out_of_memory/i);
  expect(screen.queryByText(/Model không tìm thấy gợi ý/i)).not.toBeInTheDocument();
});

it("reuses the start operation id after an uncertain failure", async () => {
  mocks.runs.mockResolvedValue({ items: [], next_cursor: null });
  mocks.suggestions.mockResolvedValue({ items: [], next_cursor: null });
  mocks.start.mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce({
    ...completedRun, id: "run-replayed", status: "queued",
  });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  const button = await screen.findByRole("button", { name: "Chạy model" });
  fireEvent.click(button);
  await screen.findByText(/Không thể đưa model/i);
  fireEvent.click(button);

  await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(2));
  expect(mocks.start.mock.calls[0][1].operation_id).toBe("operation-1");
  expect(mocks.start.mock.calls[1][1].operation_id).toBe("operation-1");
});

it("reuses the reject operation id after an uncertain failure", async () => {
  mocks.reject.mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce({
    ...suggestion, review_state: "rejected",
  });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  const button = await screen.findByRole("button", { name: "Bỏ qua" });
  fireEvent.click(button);
  await screen.findByText(/Không thể bỏ qua/i);
  fireEvent.click(button);

  await waitFor(() => expect(mocks.reject).toHaveBeenCalledTimes(2));
  expect(mocks.reject.mock.calls[0][2].operation_id).toBe("operation-1");
  expect(mocks.reject.mock.calls[1][2].operation_id).toBe("operation-1");
});

it("shows persisted run history and lets the operator inspect reviewed suggestions", async () => {
  mocks.runs.mockResolvedValue({
    items: [
      completedRun,
      { ...completedRun, id: "failed-new", status: "failed", error_code: "out_of_memory", created_at: "2026-09-13T00:02:00Z" },
    ],
    next_cursor: null,
  });
  mocks.suggestions.mockResolvedValue({ items: [], next_cursor: null });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  expect(await screen.findByText(/Lịch sử model/i)).toBeVisible();
  expect(screen.getAllByText(/Thất bại.*out_of_memory/i)).not.toHaveLength(0);
  fireEvent.change(screen.getByLabelText("Trạng thái gợi ý"), { target: { value: "rejected" } });
  await waitFor(() => expect(mocks.suggestions).toHaveBeenLastCalledWith("clip", "rejected"));
});

it("loads a bounded next page of suggestions on demand", async () => {
  const second = { ...suggestion, id: "suggestion-2", view_start_frame: 150, view_end_frame: 160 };
  mocks.suggestions
    .mockResolvedValueOnce({ items: [suggestion], next_cursor: "page-2" })
    .mockResolvedValueOnce({ items: [second], next_cursor: null });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  fireEvent.click(await screen.findByRole("button", { name: "Tải thêm gợi ý" }));

  expect(await screen.findByRole("button", { name: /Đoạn 150–160/ })).toBeVisible();
  expect(mocks.suggestions).toHaveBeenLastCalledWith("clip", "pending", undefined, "page-2");
});

it("drops a late suggestion page after the state filter changes", async () => {
  let resolvePage!: (value: { items: AssistanceSuggestionView[]; next_cursor: null }) => void;
  const latePage = new Promise<{ items: AssistanceSuggestionView[]; next_cursor: null }>(
    (resolve) => { resolvePage = resolve; },
  );
  const rejected = { ...suggestion, id: "rejected", review_state: "rejected" as const };
  const stalePending = { ...suggestion, id: "late-pending", view_start_frame: 175 };
  mocks.suggestions
    .mockResolvedValueOnce({ items: [suggestion], next_cursor: "page-2" })
    .mockReturnValueOnce(latePage)
    .mockResolvedValueOnce({ items: [rejected], next_cursor: null });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  fireEvent.click(await screen.findByRole("button", { name: "Tải thêm gợi ý" }));
  fireEvent.change(screen.getByLabelText("Trạng thái gợi ý"), {
    target: { value: "rejected" },
  });
  resolvePage({ items: [stalePending], next_cursor: null });

  expect(await screen.findByText("Đã bỏ qua")).toBeVisible();
  await waitFor(() => expect(screen.queryByText(/Đoạn 175/)).not.toBeInTheDocument());
});

it("loads active runs independently and paginates retained history", async () => {
  const activeRun = {
    ...completedRun, id: "active", status: "running" as const,
    processed_frames: 2, scheduled_frames: 10,
  };
  const olderRun = { ...completedRun, id: "older", start_frame: 20, end_frame: 30 };
  mocks.runs.mockImplementation((
    _clipId: string, activeOnly: boolean, _signal?: AbortSignal,
    cursor?: string | null,
  ) => {
    if (activeOnly) return Promise.resolve({ items: [activeRun], next_cursor: null });
    if (cursor === "history-2") return Promise.resolve({ items: [olderRun], next_cursor: null });
    return Promise.resolve({ items: [completedRun], next_cursor: "history-2" });
  });
  render(<AssistedReviewPanel clipId="clip" clipRevision={4} frameCount={200}
    currentFrame={10} onSeek={vi.fn()} onUseSuggestion={vi.fn()} onQueueChanged={vi.fn()} />);

  expect(await screen.findByText("Đang chạy 2/10")).toBeVisible();
  fireEvent.click(screen.getByText(/Lịch sử model/i));
  fireEvent.click(screen.getByRole("button", { name: "Tải thêm lịch sử" }));

  expect(await screen.findByText(/frame 20–30/)).toBeVisible();
  expect(mocks.runs).toHaveBeenCalledWith(
    "clip", false, undefined, "history-2", 5,
  );
});
