"""Douglas-Peucker track simplification."""
from __future__ import annotations

from typing import List, Sequence, Tuple

Point = Tuple[float, float]  # (lat, lon) or generic 2D


def _perp_dist(p: Point, a: Point, b: Point) -> float:
    """Perpendicular distance from p to segment a–b (euclidean on lat/lon)."""
    if a == b:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
    t = ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / (
        (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
    )
    t = max(0.0, min(1.0, t))
    proj = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return ((p[0] - proj[0]) ** 2 + (p[1] - proj[1]) ** 2) ** 0.5


def douglas_peucker(points: Sequence[Point], epsilon: float) -> List[Point]:
    """Simplify polyline; epsilon in same units as coordinates (e.g. degrees)."""
    if len(points) < 3:
        return list(points)
    dmax = 0.0
    idx = 0
    end = len(points) - 1
    for i in range(1, end):
        d = _perp_dist(points[i], points[0], points[end])
        if d > dmax:
            idx, dmax = i, d
    if dmax > epsilon:
        left = douglas_peucker(points[: idx + 1], epsilon)
        right = douglas_peucker(points[idx:], epsilon)
        return left[:-1] + right
    return [points[0], points[end]]
