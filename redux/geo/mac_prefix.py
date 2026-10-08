"""Query sightings by MAC prefix (OUI-style)."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def _norm(mac: str) -> str:
    return (mac or "").lower().replace(":", "").replace("-", "")


def query_mac_prefix(
    store: SightingStore,
    prefix: str,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    p = _norm(prefix)
    if not p:
        return []
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if _norm(s.mac).startswith(p)]
