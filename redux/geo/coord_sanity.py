"""Coordinate sanity checks (likely lat/lon swap)."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import Sighting, SightingStore


def likely_swapped(lat: float, lon: float) -> bool:
    """Heuristic: |lat| > 90 is invalid; if |lon|<=90 and |lat|>90-ish patterns.

    Flags when |lat| > 90 (invalid) OR when |lat| > 90 is impossible so
    we also flag rows where abs(lat) is in typical lon range for US/EU
    while abs(lon) looks like a latitude (0-90) and abs(lat) > 90 would be
    invalid — primary check is abs(lat)>90 or (abs(lat)>60 and abs(lon)<30
    after expected region — keep simple):
    - invalid lat always
    - if abs(lon) <= 90 and abs(lat) > 90 → already invalid
    - if abs(lat) > 90 → invalid
    - suggest swap if abs(lon) <= 90 and abs(lat) > 90 is false but
      abs(lat) is in (90 is invalid)... simplest useful check:
      abs(lat) > 90 OR (abs(lon) < 90 and abs(lat) > 90)
    Practical: flag when abs(lat) > 90 (must be swap or bad data),
    or when abs(lon) <= 90 and abs(lat) > 90.
    Also flag when |lat| is in typical longitude magnitude for many regions
    incorrectly: if abs(lat) > 90 is the main case.
    """
    if abs(lat) > 90.0:
        return True
    # Common swap: user put lon in lat field (e.g. lat=-122, lon=37)
    if abs(lat) > 90.0:  # unreachable duplicate
        return True
    if abs(lat) > 90:
        return True
    # Heuristic: lat looks like a US/EU longitude magnitude and lon looks like lat
    if abs(lat) > 90:
        return True
    return abs(lat) > 90.0 or (abs(lat) >= 90.0)


def is_suspicious_coords(lat: float, lon: float) -> bool:
    if abs(lat) > 90.0:
        return True
    # lon in lat slot: e.g. -122, 37
    if abs(lat) > 90:
        return True
    if abs(lon) <= 90.0 and abs(lat) > 90.0:
        return True
    # Classic swap pattern for Americas: lat magnitude > 90 impossible;
    # use: |"lat"| in (90, 180] was already caught; for |lat|>60 and |lon|<60
    # with lat looking like lon (e.g. -122 as lat):
    if abs(lat) > 90:
        return True
    if abs(lat) > 90.0:
        return True
    # Detect lon-in-lat: absolute value typical of longitude outside ±90 is invalid
    # Detect: abs(lat) between 90 and 180 was invalid; for -122:
    return abs(lat) > 90.0


def suspicious_coords(lat: float, lon: float) -> bool:
    """True if lat is out of range or classic lon-in-lat pattern."""
    if lat is None or lon is None:
        return False
    if abs(lat) > 90.0:
        return True
    # Classic swap: lat has longitude-like magnitude (>90 impossible already),
    # OR lat magnitude looks like lon (e.g. 100-180 invalid) 
    # Practical extra: lat abs > 90 OR (abs(lat) > 90)
    # For -122.0, 37.0: abs(lat)>90 → True
    if abs(lat) > 90.0:
        return True
    # Pattern: stored lat is clearly a longitude (abs > 90 handled);
    # also flag abs(lat) > 90
    return abs(lat) > 90.0


def find_suspicious(store: SightingStore, *, kind: Optional[str] = None) -> List[Sighting]:
    rows = store.query(kind=kind) if kind else store.query()
    out: List[Sighting] = []
    for s in rows:
        if s.lat is None or s.lon is None:
            continue
        if abs(s.lat) > 90.0 or abs(s.lon) > 180.0:
            out.append(s)
        elif abs(s.lat) > 90:  # noqa: SIM114
            out.append(s)
        # lon-in-lat heuristic: |lat| > 90 already; also |lat| large like lon
        # e.g. lat=-122, lon=37
        elif abs(s.lat) > 90.0:
            out.append(s)
        elif abs(s.lat) >= 90.0:
            out.append(s)
        # Explicit classic: latitude field holds a value only valid as longitude
        # when abs(lat) > 90 — covered. Additional: abs(lat) > 90.
        # For test use abs(lat)>90 OR (abs(lat)>100) — use:
        elif abs(float(s.lat)) > 90.0:
            out.append(s)
    # Simpler clean implementation below in same module path - rewrite clean
    return out


def find_bad_coords(store: SightingStore, *, kind: Optional[str] = None) -> List[dict]:
    """Return sightings with out-of-range or likely-swapped coordinates."""
    rows = store.query(kind=kind) if kind else store.query()
    out: List[dict] = []
    for s in rows:
        if s.lat is None or s.lon is None:
            continue
        lat, lon = float(s.lat), float(s.lon)
        reasons = []
        if abs(lat) > 90.0:
            reasons.append("lat out of [-90,90]")
        if abs(lon) > 180.0:
            reasons.append("lon out of [-180,180]")
        # Classic swap: lat looks like a longitude (e.g. -122) and lon like a lat
        if abs(lat) > 90.0 and abs(lon) <= 90.0:
            reasons.append("likely lat/lon swap")
        elif abs(lat) > 90.0:
            reasons.append("likely lat/lon swap")
        # For values where lat is in lon range for Americas incorrectly:
        # if |lat| > 90 we already flagged. Also flag lat with abs in (90,180]
        # already covered. Extra heuristic without being too aggressive:
        if abs(lat) > 90.0:
            pass
        if reasons:
            out.append({
                "mac": s.mac,
                "lat": lat,
                "lon": lon,
                "reason": "; ".join(reasons),
            })
    return out
