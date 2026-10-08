"""Top-N strongest RSSI sightings."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def top_rssi(
    store: SightingStore,
    n: int = 10,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    scored = [s for s in rows if s.rssi is not None]
    scored.sort(key=lambda s: s.rssi or -999, reverse=True)
    return scored[: max(0, n)]
