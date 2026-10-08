"""Query sightings whose SSID starts with a prefix."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def query_ssid_prefix(
    store: SightingStore,
    prefix: str,
    *,
    kind: Optional[str] = None,
    case_insensitive: bool = True,
) -> List[Sighting]:
    p = (prefix or "").strip()
    if not p:
        return []
    if case_insensitive:
        p = p.lower()
    rows = store.query(kind=kind) if kind else store.query()
    out: List[Sighting] = []
    for s in rows:
        ssid = s.ssid or ""
        hay = ssid.lower() if case_insensitive else ssid
        if hay.startswith(p):
            out.append(s)
    return out
