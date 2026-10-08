"""Per-BSSID revisit / time-of-day analysis from sighting timestamps."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Sequence

from redux.geo.db import Sighting, SightingStore


@dataclass(frozen=True)
class RevisitInfo:
    mac: str
    kind: str
    observations: int
    first_ts: float
    last_ts: float
    span_s: float
    hour_histogram: dict
    reason: str


def _hour_utc(ts: float) -> int:
    return datetime.fromtimestamp(ts, tz=timezone.utc).hour


def revisit_for_mac(
    store: SightingStore,
    mac: str,
    *,
    kind: str = "wifi",
) -> Optional[RevisitInfo]:
    row = store.get(kind, mac)
    if row is None:
        return None
    first = row.first_seen if row.first_seen is not None else row.ts
    last = row.ts
    hours: Counter[int] = Counter()
    hours[_hour_utc(first)] += 1
    if last != first:
        hours[_hour_utc(last)] += 1
    obs = 2 if last != first else 1
    reason = (
        f"revisit: {kind}/{row.mac} first={first:.0f} last={last:.0f} "
        f"span={last - first:.0f}s hours_utc={dict(hours)}"
    )
    return RevisitInfo(
        mac=row.mac,
        kind=kind,
        observations=obs,
        first_ts=float(first),
        last_ts=float(last),
        span_s=float(last - first),
        hour_histogram=dict(hours),
        reason=reason,
    )


def hour_histogram_from_sightings(sightings: Sequence[Sighting]) -> Dict[int, int]:
    """UTC hour-of-day histogram across a list of sightings."""
    c: Counter[int] = Counter()
    for s in sightings:
        if s.ts is None:
            continue
        c[_hour_utc(float(s.ts))] += 1
    return dict(c)
