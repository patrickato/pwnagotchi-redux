"""Channel occupancy statistics from the sighting store."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Tuple

from redux.geo.db import SightingStore


def channel_counts(store: SightingStore, *, kind: Optional[str] = None) -> Dict[int, int]:
    if hasattr(store, "channel_distribution"):
        return store.channel_distribution(kind=kind)
    c: Counter[int] = Counter()
    rows = store.query(kind=kind) if kind else store.query()
    for s in rows:
        if s.channel is not None:
            c[int(s.channel)] += 1
    return dict(c)


def busiest_channels(store: SightingStore, *, limit: int = 5, kind: Optional[str] = None) -> List[Tuple[int, int]]:
    return Counter(channel_counts(store, kind=kind)).most_common(limit)


def channel_summary(store: SightingStore, *, kind: Optional[str] = None) -> dict:
    counts = channel_counts(store, kind=kind)
    total = sum(counts.values())
    top = Counter(counts).most_common(3)
    return {
        "channels": counts,
        "total_with_channel": total,
        "top": top,
        "reason": f"channel stats: {total} sightings across {len(counts)} channels; top={top}",
    }
