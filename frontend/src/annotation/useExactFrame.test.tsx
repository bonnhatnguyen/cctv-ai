import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useExactFrame } from "./useExactFrame";

function response(index: number, sourceHash: string) {
  return new Response(new Blob([`frame-${index}`], { type: "image/png" }), {
    headers: { "X-Frame-Index": String(index), "X-Source-SHA256": sourceHash },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn((blob: Blob) => `blob:${blob.size}:${Math.random()}`),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("never confirms an older response or image load after selection changes", async () => {
  const pending = new Map<number, (value: Response) => void>();
  vi.mocked(fetch).mockImplementation((url) => {
    const index = Number(String(url).split("/").at(-1));
    return new Promise<Response>((resolve) => pending.set(index, resolve));
  });
  const { result, rerender } = renderHook(
    ({ index }) => useExactFrame("clip-a", "abc", index),
    { initialProps: { index: 7 } },
  );
  rerender({ index: 8 });
  await act(async () => pending.get(8)!(response(8, "abc")));
  await waitFor(() => expect(result.current.candidate?.token.index).toBe(8));
  const current = result.current.candidate!.token;
  await act(async () => pending.get(7)!(response(7, "abc")));
  result.current.confirmLoaded({ ...current, index: 7 });
  expect(result.current.displayed).toBeNull();
  act(() => result.current.confirmLoaded(current));
  expect(result.current.displayed).toEqual(current);
});

it("rejects a frame whose response headers do not match the requested source", async () => {
  vi.mocked(fetch).mockResolvedValue(response(3, "wrong"));
  const { result } = renderHook(() => useExactFrame("clip-a", "expected", 3));
  await waitFor(() => expect(result.current.error).toMatch(/không khớp/i));
  expect(result.current.candidate).toBeNull();
  expect(result.current.displayed).toBeNull();
});
