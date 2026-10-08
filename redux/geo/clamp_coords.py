"""Clamp latitude/longitude to valid geographic ranges."""
from __future__ import annotations

from dataclasses import replace
from typing import Optional, Tuple

from redux.geo.db import Sighting


def clamp_lat(lat: float) -> float:
    return max(-90.0, min(90.0, float(lat)))


def clamp_lon(lon: float) -> float:
    # Normalize to [-180, 180]
    x = (float(lon) + 180.0) % 360.0 - 180.0
    return x


def clamp_pair(lat: float, lon: float) -> Tuple[float, float]:
    return clamp_lat(lat), clamp_lon(lon)


def clamp_sighting(s: Sighting) -> Sighting:
    lat = clamp_lat(s.lat) if s.lat is not None else None
    lon = clamp_lon(s.lon) if s.lon is not None else None
    return replace(s, lat=lat, lon=lon)
