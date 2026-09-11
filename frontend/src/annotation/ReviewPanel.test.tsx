import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReviewPanel } from "./ReviewPanel";
import type { ReviewCoverageView } from "./types.generated";

afterEach(cleanup);

const activeCoverage: ReviewCoverageView = {
  id: "coverage-active",
  clip_id: "clip",
  roi_revision_id: "roi",
  revision: 1,
  start_frame: 2,
  end_frame: 8,
  reviewed_labels: ["hand_in", "hand_out"],
  guideline_version: 1,
  active: true,
};

it("records explicit per-label coverage for an exact frame interval", async () => {
  const record = vi.fn().mockResolvedValue(true);
  const dirty = vi.fn();
  const { rerender } = render(<ReviewPanel coverage={[]} currentFrame={3} frameReady
    busy={false} error={null} onRecord={record} onDirtyChange={dirty} />);

  fireEvent.click(screen.getByRole("button", { name: /Đặt bắt đầu/ }));
  rerender(<ReviewPanel coverage={[]} currentFrame={9} frameReady busy={false}
    error={null} onRecord={record} onDirtyChange={dirty} />);
  fireEvent.click(screen.getByRole("button", { name: /Đặt kết thúc/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Hand in" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Hand out" }));

  const submit = screen.getByRole("button", { name: "Ghi nhận đã kiểm tra" });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /Tôi đã xem toàn bộ/ }));
  expect(submit).toBeEnabled();
  fireEvent.click(submit);

  expect(record).toHaveBeenCalledWith({
    start_frame: 3,
    end_frame: 9,
    reviewed_labels: ["hand_in", "hand_out"],
  });
  expect(dirty).toHaveBeenCalledWith(true);
});

it("shows unknown footage separately from active and invalidated coverage", () => {
  render(<ReviewPanel coverage={[activeCoverage, { ...activeCoverage, id: "old", active: false }]}
    currentFrame={0} frameReady busy={false} error={null}
    onRecord={vi.fn()} onDirtyChange={vi.fn()} />);

  expect(screen.getByText(/Chưa được kiểm tra vẫn là chưa biết/i)).toBeVisible();
  expect(screen.getAllByText("Frame 2–8")).toHaveLength(2);
  fireEvent.click(screen.getByText("Coverage cũ (1)"));
  expect(screen.getByText("Đã bị vô hiệu hóa")).toBeVisible();
});

it("does not allow capture while an exact frame is unavailable", () => {
  render(<ReviewPanel coverage={[]} currentFrame={4} frameReady={false} busy={false}
    error={null} onRecord={vi.fn()} onDirtyChange={vi.fn()} />);

  expect(screen.getByRole("button", { name: /Đặt bắt đầu/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Đặt kết thúc/ })).toBeDisabled();
});

it("requires a new attestation after changing the interval or reviewed labels", () => {
  const { rerender } = render(<ReviewPanel coverage={[]} currentFrame={4} frameReady
    busy={false} error={null} onRecord={vi.fn()} onDirtyChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Đặt bắt đầu/ }));
  fireEvent.click(screen.getByRole("button", { name: /Đặt kết thúc/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Hand in" }));
  const attestation = screen.getByRole("checkbox", { name: /Tôi đã xem toàn bộ/ });
  fireEvent.click(attestation);
  expect(attestation).toBeChecked();

  fireEvent.click(screen.getByRole("checkbox", { name: "Hand out" }));
  expect(attestation).not.toBeChecked();
  fireEvent.click(attestation);
  rerender(<ReviewPanel coverage={[]} currentFrame={5} frameReady busy={false}
    error={null} onRecord={vi.fn()} onDirtyChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Đặt kết thúc/ }));
  expect(attestation).not.toBeChecked();
});
