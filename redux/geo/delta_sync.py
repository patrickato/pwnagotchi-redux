"""Delta sync — export/import sightings newer than a watermark ts."""
from __future__ import annotations

from typing import List

from redux.geo.db import Sighting, SightingStore


def export_since(store: SightingStore, since_ts: float) -> List[Sighting]:
    return store.query(since=since_ts)


def import_delta(store: SightingStore, rows: List[Sighting]) -> int:
    n = 0
    for s in rows:
        store.insert(s)
        n += 1
    return n

def sync_since(source: SightingStore, target: SightingStore, since_ts: float) -> dict:
    rows = export_since(source, since_ts)
    n = import_delta(target, rows)
    return {
        "exported": len(rows),
        "imported": n,
        "since_ts": since_ts,
        "reason": f"delta sync: {n} sighting(s) with ts >= {since_ts}",
    }
