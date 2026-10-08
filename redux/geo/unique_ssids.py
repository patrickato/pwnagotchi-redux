"""List unique SSIDs from the sighting store."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import SightingStore


def unique_ssids(store: SightingStore, *, kind: Optional[str] = None) -> List[str]:
    rows = store.query(kind=kind) if kind else store.query()
    return sorted({s.ssid for s in rows if (s.ssid or "").strip()})
