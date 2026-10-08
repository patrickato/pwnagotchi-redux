"""Age-bucket histogram relative to now_ts."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from redux.geo.db import SightingStore

# (label, max_age_s inclusive upper bound); last bucket is open-ended
DEFAULT_BUCKETS: List[Tuple[str, float]] = [
    ("<1h", 3600.0),
    ("1h-1d", 86400.0),
    ("1d-7d", 604800.0),
    (">7d", float("inf")),
]


def age_buckets(
    store: SightingStore,
    now_ts: float,
    *,
    kind: Optional[str] = None,
    buckets: Optional[List[Tuple[str, float]]] = None,
) -> Dict[str, int]:
    edges = buckets or DEFAULT_BUCKETS
    counts = {label: 0 for label, _ in edges}
    rows = store.query(kind=kind) if kind else store.query()
    for s in rows:
        age = max(0.0, float(now_ts) - float(s.ts or 0.0))
        for label, upper in edges:
            if age <= upper:
                counts[label] += 1
                break
    return counts
