"""Min/max timestamps present in the store."""
from __future__ import annotations

from typing import Optional, Tuple

from redux.geo.db import SightingStore


def minmax_ts(store: SightingStore, *, kind: Optional[str] = None) -> Optional[Tuple[float, float]]:
    rows = store.query(kind=kind) if kind else store.query()
    if not rows:
        return None
    ts_vals = [float(s.ts or 0.0) for s in rows]
    return min(ts_vals), max(ts_vals)
