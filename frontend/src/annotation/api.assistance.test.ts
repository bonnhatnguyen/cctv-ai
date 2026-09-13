import { afterEach, expect, it, vi } from "vitest";
import { startAssistanceRun } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("starts an assistance run without sending a filesystem path", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ id: "run" }),
  }));
  await startAssistanceRun("clip", {
    operation_id: "op", expected_clip_revision: 4, model: "dino",
    start_frame: 10, end_frame: 80,
  });
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe("/api/v2/annotations/clips/clip/assist-runs");
  expect(String(init?.body)).toBe(JSON.stringify({
    operation_id: "op", expected_clip_revision: 4, model: "dino",
    start_frame: 10, end_frame: 80,
  }));
  expect(String(init?.body)).not.toMatch(/path|root|source/i);
});
