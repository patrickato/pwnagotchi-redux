"""Human-readable summary of a sighting store."""
from __future__ import annotations

from collections import Counter

from redux.geo.db import SightingStore


def store_summary(store: SightingStore) -> dict:
    rows = store.query()
    kinds: Counter[str] = Counter()
    with_coords = 0
    with_rssi = 0
    ssids = set()
    for s in rows:
        kinds[s.kind] += 1
        if s.lat is not None and s.lon is not None:
            with_coords += 1
        if s.rssi is not None:
            with_rssi += 1
        if s.ssid:
            ssids.add(s.ssid)
    total = len(rows)
    reason = (
        f"store summary: {total} sighting(s), kinds={dict(kinds)}, "
        f"{with_coords} with coords, {with_rssi} with RSSI, "
        f"{len(ssids)} unique SSID(s)"
    )
    return {
        "total": total,
        "kinds": dict(kinds),
        "with_coords": with_coords,
        "with_rssi": with_rssi,
        "unique_ssids": len(ssids),
        "reason": reason,
    }


def store_summary_text(store: SightingStore) -> str:
    return store_summary(store)["reason"]
