"""Sighting-store stats: top SSIDs, new-since, densest cells, counts by kind."""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Tuple

from redux.geo.db import SightingStore


def _cell_key(lat: float, lon: float, cell_deg: float = 0.01) -> str:
    # floor, not int(): int() truncates toward zero, so the cell straddling
    # lat/lon 0 would be double-width and merge the two hemispheres.
    la = math.floor(lat / cell_deg) if cell_deg else 0
    lo = math.floor(lon / cell_deg) if cell_deg else 0
    return f"{la}:{lo}"


def top_ssids(store: SightingStore, *, limit: int = 10) -> List[Tuple[str, int]]:
    c: Counter[str] = Counter()
    for s in store.query(kind="wifi"):
        if s.ssid:
            c[s.ssid] += 1
    return c.most_common(limit)


def new_since(store: SightingStore, ts: float) -> int:
    return len(store.query(since=ts))


def densest_cells(
    store: SightingStore,
    *,
    cell_deg: float = 0.01,
    limit: int = 10,
) -> List[Tuple[str, int]]:
    c: Counter[str] = Counter()
    for s in store.query():
        if s.lat is None or s.lon is None:
            continue
        c[_cell_key(s.lat, s.lon, cell_deg)] += 1
    return c.most_common(limit)


def counts_by_kind(store: SightingStore) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in store.query():
        out[s.kind] = out.get(s.kind, 0) + 1
    return out


def summary(store: SightingStore) -> dict:
    return {
        "total": store.count(),
        "by_kind": counts_by_kind(store),
        "top_ssids": top_ssids(store),
        "reason": f"store summary: {store.count()} sightings",
    }
