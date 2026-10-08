"""Heatmap data generator — grid density values for a future renderer."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

Point = Tuple[float, float]  # lat, lon


@dataclass(frozen=True)
class HeatCell:
    lat_bin: int
    lon_bin: int
    lat: float  # cell center
    lon: float
    count: int


def heatmap_grid(
    points: Sequence[Point],
    *,
    cell_deg: float = 0.001,
) -> List[HeatCell]:
    """Bucket points into lat/lon cells; return non-empty cells with counts."""
    if cell_deg <= 0:
        raise ValueError("cell_deg must be positive")
    counts: Counter[Tuple[int, int]] = Counter()
    for lat, lon in points:
        la = int(math_floor(lat / cell_deg))
        lo = int(math_floor(lon / cell_deg))
        counts[(la, lo)] += 1
    out: List[HeatCell] = []
    for (la, lo), n in sorted(counts.items()):
        out.append(
            HeatCell(
                lat_bin=la,
                lon_bin=lo,
                lat=(la + 0.5) * cell_deg,
                lon=(lo + 0.5) * cell_deg,
                count=n,
            )
        )
    return out


def math_floor(x: float) -> int:
    import math

    return math.floor(x)


def heatmap_dict(points: Sequence[Point], *, cell_deg: float = 0.001) -> Dict[str, int]:
    """Compact map 'lat_bin:lon_bin' -> count."""
    return {f"{c.lat_bin}:{c.lon_bin}": c.count for c in heatmap_grid(points, cell_deg=cell_deg)}
