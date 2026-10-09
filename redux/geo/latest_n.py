"""Latest N sightings by ts."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def latest_n(
    store: SightingStore,
    n: int = 10,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    limit = max(0, n)
    if limit == 0:
        return []
    if isinstance(store, SightingStore):
        # SpatialDB orders by time in SQLite and applies LIMIT before
        # returning rows, rather than copying the entire field cache.
        return store.query(kind=kind, limit=limit)
    rows = store.query(kind=kind) if kind else store.query()
    ranked = sorted(rows, key=lambda s: float(s.ts or 0.0), reverse=True)
    return ranked[:limit]
