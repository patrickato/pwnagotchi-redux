"""Nearest-N sightings to a point."""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from redux.geo.db import Sighting, SightingStore

_EARTH_R_M = 6_371_000.0


def _hav_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def nearest(
    store: SightingStore,
    lat: float,
    lon: float,
    *,
    n: int = 5,
    kind: Optional[str] = None,
) -> List[Tuple[Sighting, float]]:
    """Return up to n (sighting, distance_m) pairs sorted by distance."""
    rows = store.query(kind=kind) if kind else store.query()
    scored: List[Tuple[Sighting, float]] = []
    for s in rows:
        if s.lat is None or s.lon is None:
            continue
        scored.append((s, _hav_m(lat, lon, s.lat, s.lon)))
    scored.sort(key=lambda x: x[1])
    return scored[: max(0, n)]
