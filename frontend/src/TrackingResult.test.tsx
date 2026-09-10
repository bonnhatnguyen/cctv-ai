import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { TrackingResult } from "./TrackingResult";
import { JobView } from "./trackingApi";

afterEach(cleanup);
const ready: JobView = {
  id: "clip", original_name: "clip.mp4", status: "ready", stage: "ready",
  metadata: { size_bytes: 100, width: 640, height: 360, duration_ms: 120,
    fps_num: 25, fps_den: 1, frame_count_estimate: 3, codec: "h264",
    preview_supported: true, sample_aspect_ratio: "1:1" },
  processed_frames: 3, total_frames_estimate: 3, tracking_percent: 100,
  summary: { actual_device: "cpu", device_name: "CPU", processed_frames: 3,
    local_track_count: 1, inference_samples: 3, mean_inference_ms: 4,
    tracking_wall_ms_total: 12, processing_seconds: 1, effective_fps: 3,
    output_duration_ms: 120 },
  failure_code: null, source_url: "/source", result_url: "/result",
  created_at: "2026-09-10T00:00:00Z", started_at: null, finished_at: null,
};

it("replaces a failed source player with an explanation and resets for a new source", () => {
  const { rerender } = render(<TrackingResult job={ready} />);
  fireEvent.error(screen.getByLabelText("Video gốc"));
  expect(screen.queryByLabelText("Video gốc")).not.toBeInTheDocument();
  expect(screen.getByText(/không hỗ trợ xem trước video gốc/i)).toBeInTheDocument();
  expect(screen.getByLabelText("Video đã theo dõi")).toHaveAttribute("src", "/result");
  rerender(<TrackingResult job={{ ...ready, id: "next", source_url: "/next" }} />);
  expect(screen.getByLabelText("Video gốc")).toHaveAttribute("src", "/next");
});

it("explains diagnostic CPU execution only after completion", () => {
  const { rerender } = render(<TrackingResult job={ready} />);
  expect(screen.getByText(/CPU.*chẩn đoán.*chậm hơn CUDA/i)).toBeInTheDocument();
  rerender(<TrackingResult job={{ ...ready, status: "processing", stage: "loading", summary: null }} />);
  expect(screen.queryByText(/chậm hơn CUDA/i)).not.toBeInTheDocument();
  rerender(<TrackingResult job={{ ...ready, summary: { ...ready.summary!, actual_device: "cuda:0" } }} />);
  expect(screen.queryByText(/chậm hơn CUDA/i)).not.toBeInTheDocument();
});
