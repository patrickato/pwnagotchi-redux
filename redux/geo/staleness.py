"""Sighting age / staleness filters."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def age_s(sighting: Sighting, now_ts: float) -> float:
    return max(0.0, float(now_ts) - float(sighting.ts or 0.0))


def is_stale(sighting: Sighting, now_ts: float, max_age_s: float) -> bool:
    return age_s(sighting, now_ts) > max_age_s


def query_fresh(
    store: SightingStore,
    now_ts: float,
    max_age_s: float,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if not is_stale(s, now_ts, max_age_s)]


def query_stale(
    store: SightingStore,
    now_ts: float,
    max_age_s: float,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if is_stale(s, now_ts, max_age_s)]
