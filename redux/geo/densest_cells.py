"""Find densest lat/lon grid cells from store coordinates."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional

from redux.geo.db import SightingStore


@dataclass(frozen=True)
class DenseCell:
    lat_bin: int
    lon_bin: int
    lat: float
    lon: float
    count: int
    reason: str


def densest_cells(
    store: SightingStore,
    *,
    cell_deg: float = 0.01,
    limit: int = 5,
    kind: Optional[str] = None,
) -> List[DenseCell]:
    if cell_deg <= 0:
        raise ValueError("cell_deg must be positive")
    c: Counter[tuple[int, int]] = Counter()
    rows = store.query(kind=kind) if kind else store.query()
    for s in rows:
        if s.lat is None or s.lon is None:
            continue
        la = int(math.floor(s.lat / cell_deg))
        lo = int(math.floor(s.lon / cell_deg))
        c[(la, lo)] += 1
    out: List[DenseCell] = []
    for (la, lo), n in c.most_common(limit):
        lat = (la + 0.5) * cell_deg
        lon = (lo + 0.5) * cell_deg
        out.append(
            DenseCell(
                lat_bin=la,
                lon_bin=lo,
                lat=lat,
                lon=lon,
                count=n,
                reason=f"dense cell center ({lat:.5f},{lon:.5f}) count={n}",
            )
        )
    return out
