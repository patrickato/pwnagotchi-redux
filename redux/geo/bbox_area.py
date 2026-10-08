"""Approximate area (km²) of an axis-aligned bounding box."""
from __future__ import annotations

import math
from typing import Optional, Tuple

from redux.geo.db import SightingStore

_EARTH_R_KM = 6371.0


def bbox_area_km2(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> float:
    """Spherical trapezoid approximation."""
    if max_lat < min_lat:
        min_lat, max_lat = max_lat, min_lat
    if max_lon < min_lon:
        min_lon, max_lon = max_lon, min_lon
    lat1, lat2 = map(math.radians, (min_lat, max_lat))
    dlon = math.radians(max_lon - min_lon)
    # area ≈ R² * |sin lat2 - sin lat1| * |dlon|
    return abs(_EARTH_R_KM ** 2 * (math.sin(lat2) - math.sin(lat1)) * dlon)


def store_bbox_area_km2(store: SightingStore) -> Optional[Tuple[float, str]]:
    rows = [s for s in store.query() if s.lat is not None and s.lon is not None]
    if not rows:
        return None
    lats = [float(s.lat) for s in rows]
    lons = [float(s.lon) for s in rows]
    area = bbox_area_km2(min(lats), min(lons), max(lats), max(lons))
    reason = f"survey bbox area ≈ {area:.4f} km² over {len(rows)} geolocated sighting(s)"
    return area, reason
