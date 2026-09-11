import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ClipTracking } from "./ClipTracking";
import type { ClipView } from "./types.generated";
import type { JobView } from "../trackingApi";

const clip: ClipView = {
  id: "clip-a", source_job_id: "job-a", original_name: "a.mp4", revision: 1,
  preparation_state: "ready", source_state: "available", failure_code: null,
  source_sha256: "a".repeat(64), roi: null, preview_url: "/a/preview", prepared_bytes: 1,
  media: { width: 160, height: 90, sample_aspect_ratio: "1:1", fps_num: 25, fps_den: 1, frame_count: 8 },
};
const imported: JobView = {
  id: "job-a", original_name: "a.mp4", status: "imported", stage: "imported",
  metadata: { width: 160, height: 90, sample_aspect_ratio: "1:1", fps_num: 25, fps_den: 1,
    frame_count_estimate: 8, duration_ms: 320, size_bytes: 1, codec: "h264", preview_supported: true },
  summary: null, processed_frames: 0, total_frames_estimate: 8, tracking_percent: null,
  failure_code: null, source_url: "/a/source", result_url: null,
  created_at: "2026-09-11T00:00:00Z", started_at: null, finished_at: null,
};
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("explains an imported clip without starting tracking", async () => {
  const fetchMock = vi.fn(async () => json(imported));
  vi.stubGlobal("fetch", fetchMock);
  render(<ClipTracking clip={clip} />);
  fireEvent.click(screen.getByRole("button", { name: "Xem tracking của clip này" }));
  expect(await screen.findByText(/Clip này chưa chạy tracking/)).toBeVisible();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/jobs/job-a", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.queryByLabelText("Video đã theo dõi")).not.toBeInTheDocument();
});

it.each(["wrong-job", "missing", "network"])("does not display a result on %s and allows retry", async (kind) => {
  vi.stubGlobal("fetch", vi.fn(async () => {
    if (kind === "network") throw new Error("offline");
    return kind === "missing" ? json({ detail: "not_found" }, 404) : json({ ...imported, id: "wrong-job" });
  }));
  render(<ClipTracking clip={clip} />);
  fireEvent.click(screen.getByRole("button", { name: "Xem tracking của clip này" }));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText(/Kết quả của clip:/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Xem tracking của clip này" })).toBeEnabled();
});

it("discards an old response when the selected clip changes", async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
  const { rerender } = render(<ClipTracking clip={clip} />);
  fireEvent.click(screen.getByRole("button", { name: "Xem tracking của clip này" }));
  rerender(<ClipTracking clip={{ ...clip, id: "clip-b", source_job_id: "job-b", original_name: "b.mp4" }} />);
  await act(async () => finish(json(imported)));
  expect(screen.queryByText(/Kết quả của clip:/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Clip này chưa chạy tracking/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Xem tracking của clip này" })).toBeEnabled();
});
