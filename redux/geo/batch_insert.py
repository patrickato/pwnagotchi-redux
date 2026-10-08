"""Batch insert sightings into the store."""
from __future__ import annotations

from typing import Iterable, Sequence

from redux.geo.db import Sighting, SightingStore


def batch_insert(store: SightingStore, sightings: Sequence[Sighting]) -> dict:
    n = 0
    for s in sightings:
        store.insert(s)
        n += 1
    return {
        "inserted": n,
        "store_count": store.count(),
        "reason": f"batch insert: processed {n} sighting(s); store now has {store.count()}",
    }
