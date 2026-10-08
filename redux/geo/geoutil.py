"""Haversine / bearing / destination geo-utils (pure math)."""
from __future__ import annotations

import math
from typing import Tuple

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing degrees: 0 = North, clockwise."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def destination(
    lat: float,
    lon: float,
    distance_m: float,
    bearing_deg_: float,
) -> Tuple[float, float]:
    """Destination point given distance (m) and bearing (deg). Returns (lat, lon)."""
    br = math.radians(bearing_deg_)
    rlat = math.radians(lat)
    rlon = math.radians(lon)
    ang = distance_m / EARTH_RADIUS_M
    lat2 = math.asin(
        math.sin(rlat) * math.cos(ang) + math.cos(rlat) * math.sin(ang) * math.cos(br)
    )
    lon2 = rlon + math.atan2(
        math.sin(br) * math.sin(ang) * math.cos(rlat),
        math.cos(ang) - math.sin(rlat) * math.sin(lat2),
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540.0) % 360.0 - 180.0
