import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ActionTimeline } from "./ActionTimeline";
import type { ActionAnnotationView } from "./types.generated";

afterEach(cleanup);

const base: ActionAnnotationView = {
  id: "a", clip_id: "clip", roi_revision_id: "roi", interaction_id: "interaction",
  revision: 1, label: "hand_in", start_frame: 10, end_frame: 20,
  crossing_frame: 15, object_kind: "unknown", visibility: "clear",
  uncertain_labels: [], unclear_reason: null, review_state: "draft",
  guideline_version: 1, deleted: false,
};

it("renders overlapping events as separate selectable rows", () => {
  render(<ActionTimeline annotations={[base, { ...base, id: "b", label: "take_out", start_frame: 15, crossing_frame: null }]}
    frameCount={100} onSelect={vi.fn()} onConfirm={vi.fn()} onDelete={vi.fn()} onRestore={vi.fn()} />);
  expect(screen.getAllByTestId("timeline-row")).toHaveLength(2);
  expect(screen.getByRole("button", { name: /Hand in/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /Take out/ })).toBeVisible();
});
