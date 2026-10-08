"""Latest N sightings by ts."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def latest_n(
    store: SightingStore,
    n: int = 10,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    ranked = sorted(rows, key=lambda s: float(s.ts or 0.0), reverse=True)
    return ranked[: max(0, n)]
