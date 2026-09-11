import type { Point } from "./types.generated";

export type Rect = { left: number; top: number; width: number; height: number };

export function fittedImageRect(
  container: Rect,
  rasterWidth: number,
  rasterHeight: number,
  sarNum: number,
  sarDen: number,
): Rect {
  if (
    container.width <= 0 || container.height <= 0 || rasterWidth <= 0 ||
    rasterHeight <= 0 || sarNum <= 0 || sarDen <= 0
  ) {
    throw new Error("Kích thước ảnh hoặc SAR không hợp lệ.");
  }
  const displayAspect = rasterWidth * sarNum / (rasterHeight * sarDen);
  const containerAspect = container.width / container.height;
  const width = displayAspect >= containerAspect
    ? container.width
    : container.height * displayAspect;
  const height = displayAspect >= containerAspect
    ? container.width / displayAspect
    : container.height;
  return {
    left: container.left + (container.width - width) / 2,
    top: container.top + (container.height - height) / 2,
    width,
    height,
  };
}

export function normalizePoint(x: number, y: number, image: Rect): Point | null {
  if (
    x < image.left || y < image.top ||
    x > image.left + image.width || y > image.top + image.height
  ) return null;
  return { x: (x - image.left) / image.width, y: (y - image.top) / image.height };
}

function orientation(a: Point, b: Point, c: Point) {
  return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
}

function intersects(a: Point, b: Point, c: Point, d: Point) {
  const values = [orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)];
  return values.some((value) => Math.abs(value) < 1e-12) ||
    ((values[0] > 0) !== (values[1] > 0) && (values[2] > 0) !== (values[3] > 0));
}

export function polygonError(points: Point[]): string | null {
  if (points.length < 3) return "Cần ít nhất 3 đỉnh.";
  if (new Set(points.map((point) => `${point.x}:${point.y}`)).size !== points.length) {
    return "Các đỉnh không được trùng nhau.";
  }
  const area = Math.abs(points.reduce((sum, point, index) => {
    const next = points[(index + 1) % points.length];
    return sum + point.x * next.y - next.x * point.y;
  }, 0));
  if (area < 1e-12) return "Vùng ROI phải có diện tích.";
  for (let first = 0; first < points.length; first += 1) {
    for (let second = first + 1; second < points.length; second += 1) {
      if (second === first + 1 || (first === 0 && second === points.length - 1)) continue;
      if (intersects(
        points[first], points[(first + 1) % points.length],
        points[second], points[(second + 1) % points.length],
      )) return "Các cạnh ROI không được cắt hoặc chạm nhau.";
    }
  }
  return null;
}
