"""Bounding-box query helper for the sighting store."""
from __future__ import annotations

from typing import List, Optional, Tuple

from redux.geo.db import Sighting, SightingStore

BBox = Tuple[float, float, float, float]  # min_lat, min_lon, max_lat, max_lon


def in_bbox(lat: float, lon: float, bbox: BBox) -> bool:
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def query_bbox(
    store: SightingStore,
    bbox: BBox,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    """Return sightings with coordinates inside bbox (inclusive)."""
    rows = store.query(kind=kind) if kind else store.query()
    out: List[Sighting] = []
    for s in rows:
        if s.lat is None or s.lon is None:
            continue
        if in_bbox(s.lat, s.lon, bbox):
            out.append(s)
    return out
