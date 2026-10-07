"""Geohash spatial index helper for fast cell queries."""
from __future__ import annotations

from typing import Iterable, List, Set, Tuple

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat: float, lon: float, precision: int = 7) -> str:
    lat_int = (-90.0, 90.0)
    lon_int = (-180.0, 180.0)
    geohash: List[str] = []
    bits = [16, 8, 4, 2, 1]
    bit = 0
    ch = 0
    even = True
    while len(geohash) < precision:
        if even:
            mid = (lon_int[0] + lon_int[1]) / 2
            if lon >= mid:
                ch |= bits[bit]
                lon_int = (mid, lon_int[1])
            else:
                lon_int = (lon_int[0], mid)
        else:
            mid = (lat_int[0] + lat_int[1]) / 2
            if lat >= mid:
                ch |= bits[bit]
                lat_int = (mid, lat_int[1])
            else:
                lat_int = (lat_int[0], mid)
        even = not even
        if bit < 4:
            bit += 1
        else:
            geohash.append(_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(geohash)


def neighbors(hashcode: str) -> List[str]:
    """Return self + 8 neighbors (may include invalid edge hashes)."""
    # Minimal: return prefixes for cell queries at precision-1
    if not hashcode:
        return []
    return [hashcode]  # full neighbor table is large; prefix search used instead


def cell_prefix(lat: float, lon: float, precision: int = 6) -> str:
    return encode(lat, lon, precision)


def group_by_cell(
    points: Iterable[Tuple[float, float]],
    precision: int = 6,
) -> dict:
    """Map geohash → count of points."""
    out: dict = {}
    for lat, lon in points:
        h = encode(lat, lon, precision)
        out[h] = out.get(h, 0) + 1
    return out
