"""Query sightings in [start_ts, end_ts]."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def query_ts_range(
    store: SightingStore,
    start_ts: float,
    end_ts: float,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if start_ts <= float(s.ts or 0.0) <= end_ts]
