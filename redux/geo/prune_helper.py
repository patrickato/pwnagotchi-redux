"""Glass-box helper around SightingStore.prune."""
from __future__ import annotations

from typing import Optional

from redux.geo.db import SightingStore


def prune_older_than(
    store: SightingStore,
    age_s: float,
    *,
    now: Optional[float] = None,
) -> dict:
    """Remove sightings older than age_s seconds (relative to now)."""
    before = store.count()
    deleted = store.prune(older_than=age_s, now=now)
    after = store.count()
    return {
        "deleted": deleted,
        "remaining": after,
        "age_s": age_s,
        "reason": (
            f"prune: removed {deleted} row(s) older than {age_s}s, "
            f"remaining={after} (before={before})"
        ),
    }
