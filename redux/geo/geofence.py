"""Geofence: point-in-polygon against a GeoJSON polygon."""
from __future__ import annotations

from typing import Any, List, Mapping, Sequence, Tuple

Point = Tuple[float, float]  # (lon, lat) GeoJSON order OR we accept (lat,lon) API


def _ray_crosses(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> bool:
    """Standard ray-cast edge test (x=lon, y=lat)."""
    if ay > by:
        ax, ay, bx, by = bx, by, ax, ay
    if py == ay or py == by:
        py = math_nextafter(py)
    if py < ay or py >= by:
        return False
    if px >= max(ax, bx):
        return False
    if px < min(ax, bx):
        return True
    xinters = ax + (py - ay) / (by - ay) * (bx - ax) if by != ay else ax
    return px < xinters


def math_nextafter(y: float) -> float:
    return y + 1e-12


def point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    inside = False
    n = len(ring)
    if n < 3:
        return False
    for i in range(n):
        ax, ay = float(ring[i][0]), float(ring[i][1])
        bx, by = float(ring[(i + 1) % n][0]), float(ring[(i + 1) % n][1])
        if _ray_crosses(lon, lat, ax, ay, bx, by):
            inside = not inside
    return inside


def point_in_geojson_polygon(lat: float, lon: float, geojson: Mapping[str, Any]) -> bool:
    """True if (lat, lon) is inside a GeoJSON Polygon or MultiPolygon.

    Coordinates in the GeoJSON are [lon, lat] per RFC 7946.
    """
    t = geojson.get("type")
    coords = geojson.get("coordinates")
    if t == "Feature":
        return point_in_geojson_polygon(lat, lon, geojson.get("geometry") or {})
    if t == "FeatureCollection":
        return any(
            point_in_geojson_polygon(lat, lon, f.get("geometry") or {})
            for f in geojson.get("features") or []
        )
    if t == "Polygon" and coords:
        exterior = coords[0]
        if not point_in_ring(lon, lat, exterior):
            return False
        for hole in coords[1:]:
            if point_in_ring(lon, lat, hole):
                return False
        return True
    if t == "MultiPolygon" and coords:
        return any(
            point_in_geojson_polygon(lat, lon, {"type": "Polygon", "coordinates": poly})
            for poly in coords
        )
    return False


def inside_authorized_area(lat: float, lon: float, geojson: Mapping[str, Any]) -> dict:
    """Glass-box result for authorized-area checks."""
    ok = point_in_geojson_polygon(lat, lon, geojson)
    return {
        "inside": ok,
        "lat": lat,
        "lon": lon,
        "reason": (
            f"point ({lat:.5f},{lon:.5f}) is "
            f"{'inside' if ok else 'outside'} the configured GeoJSON polygon"
        ),
    }
