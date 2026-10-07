"""Return-to-signal: bearing + distance to a BSSID's peak-RSSI sighting."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from redux.geo.db import SightingStore

_EARTH_R_M = 6_371_000.0


@dataclass(frozen=True)
class ReturnToSignal:
    lat: float
    lon: float
    distance_m: float
    bearing_deg: float  # 0=N, clockwise
    rssi: Optional[int]
    reason: str


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _hav_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def return_to_signal(
    store: SightingStore,
    mac: str,
    current_lat: float,
    current_lon: float,
    *,
    kind: str = "wifi",
) -> Optional[ReturnToSignal]:
    row = store.get(kind, mac)
    if row is None or row.lat is None or row.lon is None:
        return None
    dist = _hav_m(current_lat, current_lon, row.lat, row.lon)
    brg = _bearing(current_lat, current_lon, row.lat, row.lon)
    reason = (
        f"return-to-signal: BSSID {row.mac} peak sighting at "
        f"({row.lat:.5f},{row.lon:.5f}) rssi={row.rssi}; "
        f"{dist:.0f} m at bearing {brg:.0f}°"
    )
    return ReturnToSignal(
        lat=float(row.lat),
        lon=float(row.lon),
        distance_m=dist,
        bearing_deg=brg,
        rssi=row.rssi,
        reason=reason,
    )
