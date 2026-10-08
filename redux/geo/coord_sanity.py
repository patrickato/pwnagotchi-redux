"""Coordinate sanity checks (out-of-range and likely lat/lon swap)."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import SightingStore


def find_bad_coords(store: SightingStore, *, kind: Optional[str] = None) -> List[dict]:
    """Return sightings with invalid or likely-swapped coordinates."""
    rows = store.query(kind=kind) if kind else store.query()
    out: List[dict] = []
    for s in rows:
        if s.lat is None or s.lon is None:
            continue
        lat, lon = float(s.lat), float(s.lon)
        reasons: List[str] = []
        if abs(lat) > 90.0:
            reasons.append("lat out of [-90,90]")
            if abs(lon) <= 90.0:
                reasons.append("likely lat/lon swap")
        if abs(lon) > 180.0:
            reasons.append("lon out of [-180,180]")
        if reasons:
            out.append({
                "mac": s.mac,
                "lat": lat,
                "lon": lon,
                "reason": "; ".join(reasons),
            })
    return out
