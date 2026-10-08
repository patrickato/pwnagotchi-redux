"""Copy all sightings from source store into target store."""
from __future__ import annotations

from redux.geo.db import SightingStore


def copy_store(source: SightingStore, target: SightingStore) -> dict:
    n = 0
    for s in source.query():
        target.insert(s)
        n += 1
    return {
        "copied": n,
        "target_count": target.count(),
        "reason": f"copy_store: copied {n} sighting(s)",
    }
