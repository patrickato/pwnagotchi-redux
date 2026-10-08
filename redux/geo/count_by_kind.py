"""Count sightings grouped by kind."""
from __future__ import annotations

from collections import Counter
from typing import Dict

from redux.geo.db import SightingStore


def count_by_kind(store: SightingStore) -> Dict[str, int]:
    c: Counter[str] = Counter()
    for s in store.query():
        c[s.kind] += 1
    return dict(c)


def count_by_kind_summary(store: SightingStore) -> dict:
    counts = count_by_kind(store)
    total = sum(counts.values())
    return {
        "counts": counts,
        "total": total,
        "reason": f"counts by kind: {counts} (total={total})",
    }
