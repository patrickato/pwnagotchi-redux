"""Find sightings with empty / hidden SSID."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def query_empty_ssid(store: SightingStore, *, kind: Optional[str] = None) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if not (s.ssid or "").strip()]


def count_empty_ssid(store: SightingStore, *, kind: Optional[str] = None) -> dict:
    hits = query_empty_ssid(store, kind=kind)
    return {
        "count": len(hits),
        "reason": f"empty-SSID sightings: {len(hits)}",
    }
