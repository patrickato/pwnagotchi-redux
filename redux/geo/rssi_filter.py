"""Filter sightings by RSSI range."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def filter_by_rssi(
    sightings: List[Sighting],
    *,
    min_rssi: Optional[int] = None,
    max_rssi: Optional[int] = None,
) -> List[Sighting]:
    out: List[Sighting] = []
    for s in sightings:
        if s.rssi is None:
            continue
        if min_rssi is not None and s.rssi < min_rssi:
            continue
        if max_rssi is not None and s.rssi > max_rssi:
            continue
        out.append(s)
    return out


def query_by_rssi(
    store: SightingStore,
    *,
    min_rssi: Optional[int] = None,
    max_rssi: Optional[int] = None,
    kind: Optional[str] = None,
) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return filter_by_rssi(rows, min_rssi=min_rssi, max_rssi=max_rssi)
