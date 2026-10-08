"""Sightings whose first_seen falls in a time window."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def query_first_seen_window(
    store: SightingStore,
    start_ts: float,
    end_ts: float,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts
    rows = store.query(kind=kind) if kind else store.query()
    out: List[Sighting] = []
    for s in rows:
        fs = s.first_seen if s.first_seen is not None else s.ts
        if start_ts <= float(fs or 0.0) <= end_ts:
            out.append(s)
    return out
