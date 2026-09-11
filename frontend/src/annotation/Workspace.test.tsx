import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Workspace } from "./Workspace";
import type { ClipView } from "./types.generated";

const ready: ClipView = {
  schema_version: 1,
  id: "11111111-1111-4111-8111-111111111111",
  source_job_id: "22222222-2222-4222-8222-222222222222",
  original_name: "shop.mp4",
  revision: 1,
  preparation_state: "ready",
  source_state: "available",
  failure_code: null,
  source_sha256: "a".repeat(64),
  media: { frame_count: 8, fps_num: 25, fps_den: 1, width: 160, height: 90, sample_aspect_ratio: "1:1" },
  roi: null,
  preview_url: "/api/v2/annotations/clips/111/preview",
  prepared_bytes: 1234,
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:frame"), revokeObjectURL: vi.fn() });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("opens tracking for the selected V2 clip instead of the unrelated V1 job", async () => {
  localStorage.setItem("v1-active-tracking-job", "unrelated-v1-job");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/v2/annotations/storage") return json({ used_bytes: 1, limit_bytes: 100, free_bytes: 100 });
    if (url.includes("/frames/")) return new Response(new Blob(["png"]), { headers: { "X-Frame-Index": "0", "X-Source-SHA256": ready.source_sha256! } });
    if (url.startsWith("/api/v2/annotations/setups")) return json({ items: [], next_cursor: null });
    if (url.startsWith("/api/v2/annotations/clips")) return json({ items: [ready], next_cursor: null });
    if (url === `/api/v1/jobs/${ready.source_job_id}`) return json({
      id: ready.source_job_id, original_name: "shop.mp4", status: "ready", stage: "ready",
      metadata: { ...ready.media, size_bytes: 1, duration_ms: 320, frame_count_estimate: 8, codec: "h264", preview_supported: true },
      processed_frames: 8, total_frames_estimate: 8, tracking_percent: 100,
      summary: { actual_device: "cpu", device_name: "CPU", processed_frames: 8, local_track_count: 1,
        inference_samples: 0, mean_inference_ms: null, tracking_wall_ms_total: 1, processing_seconds: 1, effective_fps: 8, output_duration_ms: 320 },
      failure_code: null, source_url: "/selected/source", result_url: "/selected/result",
      created_at: "2026-09-11T00:00:00Z", started_at: null, finished_at: null,
    });
    throw new Error(`Unexpected ${url}`);
  }));
  render(<Workspace initialJobId="unrelated-v1-job" onBack={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Mở shop.mp4/i }));
  fireEvent.click(screen.getByRole("button", { name: "Xem tracking của clip này" }));
  expect(await screen.findByLabelText("Video đã theo dõi")).toHaveAttribute("src", "/selected/result");
  expect(screen.getByLabelText("Video gốc")).toHaveAttribute("src", "/selected/source");
  expect(localStorage.getItem("v1-active-tracking-job")).toBe("unrelated-v1-job");
});

it("registers an imported job without calling tracking start", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/v2/annotations/storage") return json({ used_bytes: 1234, limit_bytes: 20 * 1024 ** 3, free_bytes: 100 * 1024 ** 3 });
    if (url === "/api/v2/annotations/clips" && init?.method === "POST") return json({ ...ready, preparation_state: "preparing", media: null, source_sha256: null, preview_url: null }, 201);
    if (url.includes("/frames/0")) return new Response(new Blob(["png"]), { headers: { "X-Frame-Index": "0", "X-Source-SHA256": ready.source_sha256! } });
    if (url.includes(ready.id)) return json(ready);
    if (url.startsWith("/api/v2/annotations/clips")) return json({ items: [], next_cursor: null });
    if (url.startsWith("/api/v2/annotations/setups")) return json({ items: [], next_cursor: null });
    throw new Error(`unexpected ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Workspace initialJobId={ready.source_job_id} onBack={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Khoanh rổ tiền" }));
  await waitFor(() => expect(screen.getByText("shop.mp4")).toBeVisible());
  expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/start"), expect.anything());
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v2/annotations/clips",
    expect.objectContaining({ method: "POST" }),
  );
});

it("keeps the ROI draft when the server reports a revision conflict", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/v2/annotations/storage") return json({ used_bytes: 1234, limit_bytes: 20 * 1024 ** 3, free_bytes: 100 * 1024 ** 3 });
    if (url.includes("/frames/0")) return new Response(new Blob(["png"]), { headers: { "X-Frame-Index": "0", "X-Source-SHA256": ready.source_sha256! } });
    if (url.endsWith("/setups") && init?.method === "POST") return json({ id: "33333333-3333-4333-8333-333333333333", name: "Quầy 1", revision: 0, template: null }, 201);
    if (url.endsWith("/roi") && init?.method === "PUT") return json({ detail: "annotation_conflict" }, 409);
    if (url.startsWith("/api/v2/annotations/setups")) return json({ items: [], next_cursor: null });
    if (url.startsWith("/api/v2/annotations/clips")) return json({ items: [ready], next_cursor: null });
    throw new Error(`unexpected ${url}`);
  }));
  render(<Workspace onBack={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Mở shop.mp4/i }));
  fireEvent.load(await screen.findByTestId("exact-frame"));
  fireEvent.click(screen.getByRole("button", { name: "Tạo camera" }));
  fireEvent.change(screen.getByLabelText("Tên camera"), { target: { value: "Quầy 1" } });
  fireEvent.click(screen.getByRole("button", { name: "Lưu camera" }));
  await screen.findByText("Quầy 1");
  const stage = screen.getByTestId("media-plane");
  Object.defineProperty(stage, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 800, height: 450, right: 800, bottom: 450 }) });
  fireEvent.click(stage, { clientX: 100, clientY: 100 });
  fireEvent.click(stage, { clientX: 500, clientY: 100 });
  fireEvent.click(stage, { clientX: 500, clientY: 350 });
  fireEvent.click(screen.getByRole("button", { name: "Đóng vùng" }));
  fireEvent.click(screen.getByRole("button", { name: "Lưu ROI" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/xung đột/i);
  expect(screen.getAllByTestId("roi-point")).toHaveLength(3);
});

it("does not switch clips when the current ROI draft is rejected by the user", async () => {
  const second = { ...ready, id: "44444444-4444-4444-8444-444444444444", source_job_id: "55555555-5555-4555-8555-555555555555", original_name: "second.mp4" };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/v2/annotations/storage") return json({ used_bytes: 1234, limit_bytes: 20 * 1024 ** 3, free_bytes: 100 * 1024 ** 3 });
    if (url.includes("/frames/")) {
      const hash = url.includes(second.id) ? second.source_sha256! : ready.source_sha256!;
      return new Response(new Blob(["png"]), { headers: { "X-Frame-Index": "0", "X-Source-SHA256": hash } });
    }
    if (url.startsWith("/api/v2/annotations/setups")) return json({ items: [], next_cursor: null });
    if (url.startsWith("/api/v2/annotations/clips")) return json({ items: [ready, second], next_cursor: null });
    throw new Error(`unexpected ${url}`);
  }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<Workspace onBack={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Mở shop.mp4/i }));
  fireEvent.load(await screen.findByTestId("exact-frame"));
  const stage = screen.getByTestId("media-plane");
  Object.defineProperty(stage, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 800, height: 450, right: 800, bottom: 450 }) });
  fireEvent.click(stage, { clientX: 100, clientY: 100 });
  fireEvent.click(screen.getByRole("button", { name: /Mở second.mp4/i }));
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/ROI chưa lưu/i));
  expect(screen.getByRole("heading", { name: "shop.mp4" })).toBeVisible();
});
