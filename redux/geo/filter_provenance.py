"""Filter to sightings that carry non-empty glass-box provenance."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def with_provenance(store: SightingStore, *, kind: Optional[str] = None) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if (s.provenance or "").strip()]


def missing_provenance(store: SightingStore, *, kind: Optional[str] = None) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if not (s.provenance or "").strip()]
