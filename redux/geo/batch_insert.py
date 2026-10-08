"""Batch insert sightings into a store."""
from __future__ import annotations

from typing import Iterable, Sequence

from redux.geo.db import Sighting, SightingStore


def batch_insert(store: SightingStore, sightings: Iterable[Sighting]) -> int:
    n = 0
    for s in sightings:
        store.insert(s)
        n += 1
    return n
