"""Simple arithmetic mean centroid of lat/lon points."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

Point = Tuple[float, float]


@dataclass(frozen=True)
class SimpleCentroid:
    lat: float
    lon: float
    count: int
    reason: str


def mean_centroid(points: Sequence[Point]) -> Optional[SimpleCentroid]:
    if not points:
        return None
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    return SimpleCentroid(
        lat=lat,
        lon=lon,
        count=len(points),
        reason=f"mean centroid of {len(points)} point(s): ({lat:.6f},{lon:.6f})",
    )
