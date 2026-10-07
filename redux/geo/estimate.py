"""RSSI weighted-centroid AP location estimate.

Given multiple geo-tagged sightings of the same transmitter, estimate its
position as an RSSI-weighted average of observer locations. Stronger
(less negative) RSSI samples pull the centroid harder via linear power
weights 10^(rssi/10).

Returns observation count and a rough error radius (meters) derived from
the weighted RMS residual. Pure math — no GPS hardware, no engine import.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol, Sequence

# Earth radius (mean) for equirectangular residual distance.
_EARTH_R_M = 6_371_000.0


class _GeoSample(Protocol):
    lat: Optional[float]
    lon: Optional[float]
    rssi: Optional[int]


@dataclass(frozen=True)
class LocationEstimate:
    """Estimated transmitter position with glass-box confidence fields."""

    lat: float
    lon: float
    observation_count: int
    error_radius_m: float
    reason: str  # glass-box: how the estimate was formed


def _power_weight(rssi_dbm: int) -> float:
    """Relative linear power from dBm (always positive)."""
    # Clamp extreme values so a single -20 dBm sample cannot dominate infinitely
    # in pathological test data; still monotonic in rssi.
    clamped = max(-100, min(-20, int(rssi_dbm)))
    return 10.0 ** (clamped / 10.0)


def _hav_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters (haversine)."""
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def estimate_location(
    samples: Iterable[_GeoSample],
    *,
    min_observations: int = 2,
) -> Optional[LocationEstimate]:
    """Weighted-centroid estimate from samples with lat/lon/rssi.

    Samples missing lat, lon, or rssi are skipped. Returns None when fewer
    than ``min_observations`` usable samples remain.
    """
    usable: List[tuple[float, float, float]] = []  # lat, lon, weight
    for s in samples:
        if s.lat is None or s.lon is None or s.rssi is None:
            continue
        w = _power_weight(s.rssi)
        if w <= 0:
            continue
        usable.append((float(s.lat), float(s.lon), w))

    n = len(usable)
    if n < min_observations:
        return None

    w_sum = sum(w for _, _, w in usable)
    if w_sum <= 0:
        return None

    lat = sum(la * w for la, _, w in usable) / w_sum
    lon = sum(lo * w for _, lo, w in usable) / w_sum

    # Weighted RMS residual distance from centroid → rough error radius.
    # Single-point case (if min_observations==1) gets a conventional floor.
    if n == 1:
        radius = 50.0
    else:
        var = sum(w * (_hav_m(lat, lon, la, lo) ** 2) for la, lo, w in usable) / w_sum
        radius = math.sqrt(var)
        # Inflate slightly when few samples (rule of thumb).
        radius *= max(1.0, math.sqrt(4.0 / n))
        radius = max(radius, 5.0)  # floor: don't claim sub-5 m from WiFi RSSI

    reason = (
        f"RSSI-weighted centroid of {n} geo-tagged samples "
        f"(power weights 10^(rssi/10)); error_radius_m≈{radius:.1f} "
        f"from weighted RMS residual"
    )
    return LocationEstimate(
        lat=lat,
        lon=lon,
        observation_count=n,
        error_radius_m=radius,
        reason=reason,
    )


def estimate_from_coords(
    points: Sequence[tuple[float, float, int]],
    *,
    min_observations: int = 2,
) -> Optional[LocationEstimate]:
    """Convenience: list of (lat, lon, rssi_dbm) tuples."""

    class _P:
        __slots__ = ("lat", "lon", "rssi")

        def __init__(self, lat: float, lon: float, rssi: int) -> None:
            self.lat = lat
            self.lon = lon
            self.rssi = rssi

    return estimate_location(
        [_P(la, lo, r) for la, lo, r in points],
        min_observations=min_observations,
    )
