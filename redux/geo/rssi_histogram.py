"""RSSI histogram over sightings."""
from __future__ import annotations

from collections import Counter
from typing import Dict, Optional

from redux.geo.db import SightingStore


def rssi_histogram(
    store: SightingStore,
    *,
    bucket: int = 10,
    kind: Optional[str] = None,
) -> Dict[int, int]:
    """Map bucket floor (e.g. -50) -> count."""
    if bucket <= 0:
        raise ValueError("bucket must be positive")
    c: Counter[int] = Counter()
    rows = store.query(kind=kind) if kind else store.query()
    for s in rows:
        if s.rssi is None:
            continue
        floor = (int(s.rssi) // bucket) * bucket
        c[floor] += 1
    return dict(sorted(c.items()))
