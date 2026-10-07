"""Offline self-geolocation from known APs in the sighting store.

When GPS is unavailable, match currently visible BSSIDs against rows that
already have lat/lon in BeastSpatialDB and estimate own position as an
RSSI-weighted centroid of those AP locations (inverse of AP-location
estimate: observers move, APs are anchors).

Pure logic — no gpsd, no network, no redux.engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from redux.geo.db import SightingStore
from redux.geo.estimate import LocationEstimate, estimate_from_coords


@dataclass(frozen=True)
class VisibleAP:
    """One currently heard AP (from live scan)."""

    mac: str
    rssi: int
    kind: str = "wifi"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mac", (self.mac or "").lower().strip())
        object.__setattr__(self, "kind", (self.kind or "wifi").lower().strip())


def self_locate(
    store: SightingStore,
    visible: Sequence[VisibleAP],
    *,
    min_matches: int = 2,
    kind: str = "wifi",
) -> Optional[LocationEstimate]:
    """Estimate own lat/lon from visible APs that exist in the store with coords.

    Returns None if fewer than ``min_matches`` geo-tagged matches.
    """
    points: List[Tuple[float, float, int]] = []
    matched_macs: List[str] = []

    for v in visible:
        if v.kind != kind:
            continue
        row = store.get(kind, v.mac)
        if row is None or row.lat is None or row.lon is None:
            continue
        points.append((float(row.lat), float(row.lon), int(v.rssi)))
        matched_macs.append(v.mac)

    est = estimate_from_coords(points, min_observations=min_matches)
    if est is None:
        return None

    reason = (
        f"offline self-locate: RSSI-weighted centroid of {est.observation_count} "
        f"known AP(s) in the sighting store (matched {', '.join(matched_macs[:5])}"
        f"{', …' if len(matched_macs) > 5 else ''}); "
        f"error_radius_m≈{est.error_radius_m:.1f}"
    )
    return LocationEstimate(
        lat=est.lat,
        lon=est.lon,
        observation_count=est.observation_count,
        error_radius_m=est.error_radius_m,
        reason=reason,
    )


def self_locate_from_scan(
    store: SightingStore,
    scan: Iterable[Tuple[str, int]],
    *,
    min_matches: int = 2,
    kind: str = "wifi",
) -> Optional[LocationEstimate]:
    """Convenience: iterable of (mac, rssi) pairs."""
    visible = [VisibleAP(mac=m, rssi=r, kind=kind) for m, r in scan]
    return self_locate(store, visible, min_matches=min_matches, kind=kind)
