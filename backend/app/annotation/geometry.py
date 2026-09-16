from __future__ import annotations

from math import isfinite

from .contracts import Point


_EPSILON = 1e-12


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _on_segment(a: Point, b: Point, point: Point) -> bool:
    return (
        min(a.x, b.x) - _EPSILON <= point.x <= max(a.x, b.x) + _EPSILON
        and min(a.y, b.y) - _EPSILON <= point.y <= max(a.y, b.y) + _EPSILON
        and abs(_orientation(a, b, point)) <= _EPSILON
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    ab_c = _orientation(a, b, c)
    ab_d = _orientation(a, b, d)
    cd_a = _orientation(c, d, a)
    cd_b = _orientation(c, d, b)
    if ((ab_c > _EPSILON and ab_d < -_EPSILON) or (ab_c < -_EPSILON and ab_d > _EPSILON)) and (
        (cd_a > _EPSILON and cd_b < -_EPSILON) or (cd_a < -_EPSILON and cd_b > _EPSILON)
    ):
        return True
    return any(
        (
            abs(value) <= _EPSILON,
            _on_segment(start, end, point),
        )
        == (True, True)
        for value, start, end, point in (
            (ab_c, a, b, c),
            (ab_d, a, b, d),
            (cd_a, c, d, a),
            (cd_b, c, d, b),
        )
    )


def validate_polygon(points: list[Point]) -> list[Point]:
    """Return a valid simple normalized polygon without mutating its points."""

    if not 3 <= len(points) <= 64:
        raise ValueError("polygon must contain between 3 and 64 vertices")
    coordinates = [(point.x, point.y) for point in points]
    if any(not isfinite(value) for coordinate in coordinates for value in coordinate):
        raise ValueError("polygon coordinates must be finite")
    if len(set(coordinates)) != len(coordinates):
        raise ValueError("polygon vertices must be distinct and not explicitly closed")

    count = len(points)
    for index, start in enumerate(points):
        end = points[(index + 1) % count]
        if abs(start.x - end.x) <= _EPSILON and abs(start.y - end.y) <= _EPSILON:
            raise ValueError("polygon contains a zero-length edge")

    twice_area = sum(
        points[index].x * points[(index + 1) % count].y
        - points[(index + 1) % count].x * points[index].y
        for index in range(count)
    )
    if abs(twice_area) <= _EPSILON:
        raise ValueError("polygon area must be greater than zero")

    for first in range(count):
        a = points[first]
        b = points[(first + 1) % count]
        for second in range(first + 1, count):
            if second == first or second == (first + 1) % count:
                continue
            if first == 0 and second == count - 1:
                continue
            c = points[second]
            d = points[(second + 1) % count]
            if _segments_intersect(a, b, c, d):
                raise ValueError("polygon edges must not intersect or touch")
    return points
