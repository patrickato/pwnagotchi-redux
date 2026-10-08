"""Random sample of sightings (for tests / survey previews)."""
from __future__ import annotations

import random
from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def sample_sightings(
    store: SightingStore,
    k: int,
    *,
    kind: Optional[str] = None,
    seed: Optional[int] = None,
) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    if k <= 0 or not rows:
        return []
    rng = random.Random(seed)
    if k >= len(rows):
        out = list(rows)
        rng.shuffle(out)
        return out
    return rng.sample(list(rows), k)
