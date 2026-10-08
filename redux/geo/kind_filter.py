"""Filter sightings by one or more kind values."""
from __future__ import annotations

from typing import Iterable, List, Set

from redux.geo.db import Sighting, SightingStore


def filter_kinds(sightings: Iterable[Sighting], kinds: Iterable[str]) -> List[Sighting]:
    want: Set[str] = {k.lower().strip() for k in kinds if k}
    if not want:
        return list(sightings)
    return [s for s in sightings if s.kind in want]


def query_kinds(store: SightingStore, kinds: Iterable[str]) -> List[Sighting]:
    return filter_kinds(store.query(), kinds)
