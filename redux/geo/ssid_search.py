"""SSID search helpers over the sighting store."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def search_ssid(
    store: SightingStore,
    query: str,
    *,
    kind: Optional[str] = None,
    case_insensitive: bool = True,
) -> List[Sighting]:
    """Return sightings whose SSID contains query."""
    q = (query or "").strip()
    if not q:
        return []
    if case_insensitive:
        q = q.lower()
    rows = store.query(kind=kind) if kind else store.query()
    out: List[Sighting] = []
    for s in rows:
        ssid = s.ssid or ""
        hay = ssid.lower() if case_insensitive else ssid
        if q in hay:
            out.append(s)
    return out


def ssids_matching(store: SightingStore, query: str, **kwargs) -> List[str]:
    return sorted({s.ssid for s in search_ssid(store, query, **kwargs) if s.ssid})
