"""List sightings at or below a weak-RSSI threshold."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def query_weak_rssi(
    store: SightingStore,
    threshold: int = -80,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    """Return sightings with rssi <= threshold (more negative = weaker)."""
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if s.rssi is not None and int(s.rssi) <= threshold]
