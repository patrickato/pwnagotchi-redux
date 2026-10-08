"""Sorting helpers for sighting lists."""
from __future__ import annotations

from typing import List, Sequence

from redux.geo.db import Sighting


def sort_by_rssi(sightings: Sequence[Sighting], *, descending: bool = True) -> List[Sighting]:
    return sorted(
        sightings,
        key=lambda s: s.rssi if s.rssi is not None else (-999 if descending else 999),
        reverse=descending,
    )


def sort_by_ts(sightings: Sequence[Sighting], *, descending: bool = True) -> List[Sighting]:
    return sorted(sightings, key=lambda s: float(s.ts or 0.0), reverse=descending)


def sort_by_ssid(sightings: Sequence[Sighting]) -> List[Sighting]:
    return sorted(sightings, key=lambda s: (s.ssid or "").lower())
