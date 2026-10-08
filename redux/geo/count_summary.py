"""Compact counts for a sighting store."""
from __future__ import annotations

from collections import Counter

from redux.geo.db import SightingStore


def count_summary(store: SightingStore) -> dict:
    by_kind: Counter[str] = Counter()
    with_coords = 0
    with_ssid = 0
    for s in store.query():
        by_kind[s.kind] += 1
        if s.lat is not None and s.lon is not None:
            with_coords += 1
        if s.ssid:
            with_ssid += 1
    total = store.count()
    return {
        "total": total,
        "by_kind": dict(by_kind),
        "with_coords": with_coords,
        "with_ssid": with_ssid,
        "reason": (
            f"count summary: total={total}, coords={with_coords}, "
            f"ssid={with_ssid}, kinds={dict(by_kind)}"
        ),
    }
