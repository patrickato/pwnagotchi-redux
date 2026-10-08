"""Path / track length from ordered lat/lon points."""
from __future__ import annotations

import math
from typing import Sequence, Tuple

Point = Tuple[float, float]
_EARTH_R_M = 6_371_000.0


def _hav_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def track_length_m(points: Sequence[Point]) -> float:
    """Sum consecutive haversine segments."""
    if len(points) < 2:
        return 0.0
    total = 0.0
    for (a, b) in zip(points, points[1:]):
        total += _hav_m(a[0], a[1], b[0], b[1])
    return total
