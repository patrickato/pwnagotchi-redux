"""RSSI min/max/mean over the store."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from redux.geo.db import SightingStore


@dataclass(frozen=True)
class RssiStats:
    count: int
    min_rssi: Optional[int]
    max_rssi: Optional[int]
    mean_rssi: Optional[float]
    reason: str


def rssi_stats(store: SightingStore, *, kind: Optional[str] = None) -> RssiStats:
    rows = store.query(kind=kind) if kind else store.query()
    vals = [int(s.rssi) for s in rows if s.rssi is not None]
    if not vals:
        return RssiStats(0, None, None, None, "rssi stats: no samples")
    mean = sum(vals) / len(vals)
    return RssiStats(
        count=len(vals),
        min_rssi=min(vals),
        max_rssi=max(vals),
        mean_rssi=mean,
        reason=f"rssi stats: n={len(vals)} min={min(vals)} max={max(vals)} mean={mean:.1f}",
    )
