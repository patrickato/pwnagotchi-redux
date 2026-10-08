"""Glass-box helper around SightingStore.prune."""
from __future__ import annotations

from typing import Optional

from redux.geo.db import SightingStore


def prune_older_than(store: SightingStore, older_than_ts: float) -> dict:
    """Remove sightings with ts older than older_than_ts."""
    before = store.count()
    # SightingStore.prune accepts older_than as a timestamp threshold
    store.prune(older_than=older_than_ts)
    after = store.count()
    deleted = before - after
    return {
        "deleted": deleted,
        "remaining": after,
        "older_than": older_than_ts,
        "reason": f"prune: removed {deleted} row(s) with ts < {older_than_ts}, remaining={after}",
    }
