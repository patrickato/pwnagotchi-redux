"""Sightings that lack coordinates (not yet geolocated)."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def query_missing_coords(store: SightingStore, *, kind: Optional[str] = None) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if s.lat is None or s.lon is None]


def query_with_coords(store: SightingStore, *, kind: Optional[str] = None) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if s.lat is not None and s.lon is not None]
