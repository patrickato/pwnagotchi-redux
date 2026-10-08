"""Iterate store sightings in fixed-size chunks."""
from __future__ import annotations

from typing import Iterator, List, Optional

from redux.geo.db import Sighting, SightingStore


def iter_chunks(
    store: SightingStore,
    chunk_size: int = 100,
    *,
    kind: Optional[str] = None,
) -> Iterator[List[Sighting]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    rows = store.query(kind=kind) if kind else store.query()
    for i in range(0, len(rows), chunk_size):
        yield list(rows[i : i + chunk_size])
