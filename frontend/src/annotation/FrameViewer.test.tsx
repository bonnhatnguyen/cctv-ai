import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { FrameViewer } from "./FrameViewer";
import type { ClipView } from "./types.generated";

afterEach(cleanup);

it("keeps the saved ROI visible and resumes/seeks the preview at the chosen source frame", async () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  const roi = { id: "roi", revision: 1, camera_setup_id: "camera", template_revision_id: null,
    polygon: [{ x: .1, y: .1 }, { x: .8, y: .1 }, { x: .8, y: .8 }] };
  const clip: ClipView = {
    id: "clip", source_job_id: "job", original_name: "shop.mp4", revision: 1,
    preparation_state: "ready", source_state: "available", failure_code: null,
    source_sha256: "hash", media: { frame_count: 751, fps_num: 25, fps_den: 1, width: 960, height: 1080, sample_aspect_ratio: "2:1" },
    roi, preview_url: "/preview", prepared_bytes: 1,
  };
  function Viewer() {
    const [index, setIndex] = useState(375);
    const [playing, setPlaying] = useState(false);
    return <><FrameViewer clip={clip} index={index} onIndex={setIndex} playing={playing} onPlaybackChange={setPlaying}
      candidate={null} onFrameLoaded={() => undefined} onFrameError={() => undefined} points={roi.polygon} onPoint={() => undefined} />
      <div className="clip-tracking"><button type="button">Điều khiển kết quả tracking</button></div></>;
  }
  try {
    render(<Viewer />);
    fireEvent.keyDown(screen.getByRole("button", { name: "Điều khiển kết quả tracking" }), { key: " " });
    expect(screen.getByRole("button", { name: "Phát preview" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Phát preview" }));
    const video = document.querySelector("video")!;
    await act(async () => fireEvent.loadedMetadata(video));
    expect(video.currentTime).toBe(15);
    expect(screen.getByLabelText("ROI rổ tiền").querySelector("polygon")).toHaveAttribute("points", "0.1,0.1 0.8,0.1 0.8,0.8");
    expect(screen.queryByTestId("roi-point")).not.toBeInTheDocument();
    video.currentTime = 16;
    fireEvent.click(screen.getByRole("button", { name: "Tạm dừng" }));
    expect(screen.getByTestId("annotation-frame-input")).toHaveValue(400);
    fireEvent.click(screen.getByRole("button", { name: "Phát preview" }));
    const resumed = document.querySelector("video")!;
    await act(async () => fireEvent.loadedMetadata(resumed));
    expect(resumed.currentTime).toBe(16);
    fireEvent.change(screen.getByTestId("annotation-frame-input"), { target: { value: "500" } });
    expect(screen.getByRole("button", { name: "Phát preview" })).toBeVisible();
    expect(screen.getByTestId("annotation-frame-input")).toHaveValue(500);
  } finally { play.mockRestore(); pause.mockRestore(); }
});

it("applies sample aspect ratio to exact image and overlay as one plane", () => {
  const clip: ClipView = {
    schema_version: 1, id: "clip", source_job_id: "job", original_name: "shop.mp4",
    revision: 1, preparation_state: "ready", source_state: "available", failure_code: null,
    source_sha256: "hash", media: { frame_count: 3, fps_num: 25, fps_den: 1, width: 960, height: 1080, sample_aspect_ratio: "2:1" },
    roi: null, preview_url: "/preview", prepared_bytes: 1,
  };
  render(<FrameViewer clip={clip} index={0} onIndex={vi.fn()} candidate={{ token: { clipId: "clip", sourceHash: "hash", index: 0, generation: 1 }, url: "blob:test" }} onFrameLoaded={vi.fn()} onFrameError={vi.fn()} playing={false} onPlaybackChange={vi.fn()} points={[]} onPoint={vi.fn()} />);
  expect(screen.getByTestId("media-plane")).toHaveStyle({ aspectRatio: "1920 / 1080" });
  expect(screen.getByTestId("exact-frame")).toHaveStyle({ objectFit: "fill" });
  expect(screen.getByLabelText("ROI rổ tiền").parentElement).toBe(screen.getByTestId("media-plane"));
});

it("offers only the approved preview playback rates", () => {
  const clip: ClipView = {
    schema_version: 1, id: "clip", source_job_id: "job", original_name: "shop.mp4",
    revision: 1, preparation_state: "ready", source_state: "available", failure_code: null,
    source_sha256: "hash", media: { frame_count: 3, fps_num: 25, fps_den: 1, width: 160, height: 90, sample_aspect_ratio: "1:1" },
    roi: null, preview_url: "/preview", prepared_bytes: 1,
  };
  render(<FrameViewer clip={clip} index={0} onIndex={vi.fn()} candidate={null} onFrameLoaded={vi.fn()} onFrameError={vi.fn()} playing={false} onPlaybackChange={vi.fn()} points={[]} onPoint={vi.fn()} />);
  const select = screen.getByLabelText("Tốc độ preview");
  expect(Array.from(select.querySelectorAll("option"), option => option.value)).toEqual(["0.25", "0.5", "1"]);
  fireEvent.change(select, { target: { value: "0.5" } });
  expect(select).toHaveValue("0.5");
});

it("maps a dragged ROI vertex back to normalized image coordinates", () => {
  const clip: ClipView = {
    schema_version: 1, id: "clip", source_job_id: "job", original_name: "shop.mp4",
    revision: 1, preparation_state: "ready", source_state: "available", failure_code: null,
    source_sha256: "hash", media: { frame_count: 3, fps_num: 25, fps_den: 1, width: 160, height: 90, sample_aspect_ratio: "1:1" },
    roi: null, preview_url: "/preview", prepared_bytes: 1,
  };
  const moved = vi.fn();
  render(<FrameViewer clip={clip} index={0} onIndex={vi.fn()} candidate={null} onFrameLoaded={vi.fn()} onFrameError={vi.fn()} playing={false} onPlaybackChange={vi.fn()} points={[{ x: .1, y: .1 }]} onPoint={vi.fn()} onMovePoint={moved} />);
  const plane = screen.getByTestId("media-plane");
  Object.defineProperty(plane, "getBoundingClientRect", { value: () => ({ left: 10, top: 20, width: 200, height: 100, right: 210, bottom: 120 }) });
  fireEvent.pointerDown(screen.getByTestId("roi-point"));
  fireEvent.pointerMove(plane, { clientX: 110, clientY: 70 });
  fireEvent.pointerUp(plane);
  expect(moved).toHaveBeenCalledWith(0, { x: .5, y: .5 });
});
