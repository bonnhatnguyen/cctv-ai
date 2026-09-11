import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { RoiEditor } from "./RoiEditor";
import type { CameraSetupView, ClipView, Point } from "./types.generated";

afterEach(cleanup);

const clip: ClipView = {
  schema_version: 1, id: "clip", source_job_id: "job", original_name: "shop.mp4",
  revision: 1, preparation_state: "ready", source_state: "available", failure_code: null,
  source_sha256: "hash", media: { frame_count: 3, fps_num: 25, fps_den: 1, width: 160, height: 90, sample_aspect_ratio: "1:1" },
  roi: null, preview_url: "/preview", prepared_bytes: 1,
};

const templatePolygon: Point[] = [{ x: .2, y: .2 }, { x: .7, y: .2 }, { x: .7, y: .7 }];
const setup: CameraSetupView = {
  id: "setup", name: "Quầy 1", revision: 1,
  template: { id: "template", revision: 1, camera_setup_id: "setup", polygon: templatePolygon, template_revision_id: null },
};

function Harness() {
  const [points, setPoints] = useState<Point[]>([]);
  const [setups, setSetups] = useState([setup]);
  return <RoiEditor clip={clip} frameReady displayed={{ clipId: "clip", sourceHash: "hash", index: 0, generation: 1 }}
    points={points} setPoints={setPoints} setups={setups} setSetups={setSetups} onSaved={vi.fn()} />;
}

it("requires explicit confirmation before a camera template can become clip ROI", () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText("Camera"), { target: { value: "setup" } });
  fireEvent.click(screen.getByRole("button", { name: "Xem mẫu camera" }));
  expect(screen.getByText(/mẫu đang xem chưa phải ROI của clip/i)).toBeVisible();
  expect(screen.getByRole("button", { name: "Lưu ROI" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Xác nhận ROI cho clip này" }));
  expect(screen.getByRole("button", { name: "Lưu ROI" })).toBeEnabled();
});
