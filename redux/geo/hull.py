"""Axis-aligned bounding box (and simple convex hull) of sighting coords."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from redux.geo.db import Sighting, SightingStore

Point = Tuple[float, float]


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @property
    def as_tuple(self) -> Tuple[float, float, float, float]:
        return self.min_lat, self.min_lon, self.max_lat, self.max_lon


def bounding_box(points: Sequence[Point]) -> Optional[BoundingBox]:
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return BoundingBox(min(lats), min(lons), max(lats), max(lons))


def store_bounding_box(store: SightingStore, *, kind: Optional[str] = None) -> Optional[BoundingBox]:
    rows = store.query(kind=kind) if kind else store.query()
    pts = [(s.lat, s.lon) for s in rows if s.lat is not None and s.lon is not None]
    return bounding_box(pts)  # type: ignore[arg-type]


def convex_hull(points: Sequence[Point]) -> List[Point]:
    """Monotone-chain convex hull. Returns points in CCW order."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return list(pts)

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]
