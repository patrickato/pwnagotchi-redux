"""Suggest the nearest uncovered coverage cell to visit next."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

Point = Tuple[float, float]


@dataclass(frozen=True)
class GapSuggestion:
    cell_lat: float
    cell_lon: float
    distance_m: float
    reason: str


def _cell(lat: float, lon: float, cell_deg: float) -> Tuple[int, int]:
    return int(math.floor(lat / cell_deg)), int(math.floor(lon / cell_deg))


def _center(la: int, lo: int, cell_deg: float) -> Point:
    return (la + 0.5) * cell_deg, (lo + 0.5) * cell_deg


def _hav_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def next_gap(
    covered_points: Sequence[Point],
    current: Point,
    *,
    cell_deg: float = 0.01,
    search_radius_cells: int = 5,
) -> Optional[GapSuggestion]:
    """Nearest empty cell center within search radius of current position."""
    covered: Set[Tuple[int, int]] = {_cell(la, lo, cell_deg) for la, lo in covered_points}
    cla, clo = _cell(current[0], current[1], cell_deg)
    best: Optional[GapSuggestion] = None
    for dla in range(-search_radius_cells, search_radius_cells + 1):
        for dlo in range(-search_radius_cells, search_radius_cells + 1):
            if dla == 0 and dlo == 0:
                continue
            key = (cla + dla, clo + dlo)
            if key in covered:
                continue
            lat, lon = _center(key[0], key[1], cell_deg)
            dist = _hav_m(current[0], current[1], lat, lon)
            if best is None or dist < best.distance_m:
                best = GapSuggestion(
                    cell_lat=lat,
                    cell_lon=lon,
                    distance_m=dist,
                    reason=(
                        f"next gap cell center ({lat:.5f},{lon:.5f}) "
                        f"~{dist:.0f} m from current"
                    ),
                )
    return best
