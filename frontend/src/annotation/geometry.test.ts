import { expect, it } from "vitest";
import { fittedImageRect, normalizePoint } from "./geometry";

it("maps SAR 2:1 image through letterbox without stretching ROI", () => {
  const image = fittedImageRect(
    { left: 0, top: 0, width: 1000, height: 1000 },
    960,
    1080,
    2,
    1,
  );
  expect(image.height).toBeCloseTo(562.5);
  expect(image.top).toBeCloseTo(218.75);
  expect(normalizePoint(500, 500, image)).toEqual({ x: 0.5, y: 0.5 });
  expect(normalizePoint(500, 100, image)).toBeNull();
});

it("rejects invalid raster and SAR dimensions", () => {
  expect(() => fittedImageRect({ left: 0, top: 0, width: 100, height: 100 }, 0, 10, 1, 1)).toThrow();
  expect(() => fittedImageRect({ left: 0, top: 0, width: 100, height: 100 }, 10, 10, 1, 0)).toThrow();
});
