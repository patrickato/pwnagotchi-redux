"""Merge sighting lists keeping best RSSI / earliest first_seen per key."""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from redux.geo.db import Sighting


def _better(a: Sighting, b: Sighting) -> Sighting:
    """Prefer stronger RSSI; keep earliest first_seen."""
    ar = a.rssi if a.rssi is not None else -999
    br = b.rssi if b.rssi is not None else -999
    best = a if ar >= br else b
    first_a = a.first_seen if a.first_seen is not None else a.ts
    first_b = b.first_seen if b.first_seen is not None else b.ts
    first = min(float(first_a or 0.0), float(first_b or 0.0))
    from dataclasses import replace

    return replace(best, first_seen=first)


def merge_best_rssi(sightings: Sequence[Sighting]) -> List[Sighting]:
    by_key: Dict[Tuple[str, str], Sighting] = {}
    for s in sightings:
        key = (s.kind, s.mac)
        if key not in by_key:
            by_key[key] = s
        else:
            by_key[key] = _better(by_key[key], s)
    return list(by_key.values())
