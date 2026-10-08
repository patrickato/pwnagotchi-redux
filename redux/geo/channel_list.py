"""List distinct channels present in the sighting store."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import SightingStore


def list_channels(store: SightingStore, *, kind: Optional[str] = None) -> List[int]:
    rows = store.query(kind=kind) if kind else store.query()
    return sorted({int(s.channel) for s in rows if s.channel is not None})
