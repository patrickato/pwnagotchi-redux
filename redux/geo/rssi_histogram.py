"""RSSI histogram over store sightings."""
from __future__ import annotations

from collections import Counter
from typing import Dict, Optional

from redux.geo.db import SightingStore


def rssi_histogram(
    store: SightingStore,
    *,
    bucket: int = 10,
    kind: Optional[str] = None,
) -> Dict[str, int]:
    """Bucket RSSI values (e.g. -90..-81 → '-90')."""
    if bucket <= 0:
        raise ValueError("bucket must be > 0")
    c: Counter[str] = Counter()
    rows = store.query(kind=kind) if kind else store.query()
    for s in rows:
        if s.rssi is None:
            continue
        # floor toward more negative
        base = (s.rssi // bucket) * bucket
        c[str(base)] += 1
    return dict(sorted(c.items(), key=lambda kv: int(kv[0])))
