"""Dead-reckoning gap-fill across GPS dropouts from speed/heading."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

_EARTH_R_M = 6_371_000.0


@dataclass(frozen=True)
class Fix:
    ts: float
    lat: float
    lon: float
    speed_mps: float = 0.0  # m/s
    heading_deg: float = 0.0  # 0=N clockwise
    valid: bool = True


def _step(lat: float, lon: float, speed_mps: float, heading_deg: float, dt: float) -> Tuple[float, float]:
    dist = speed_mps * dt
    if dist == 0:
        return lat, lon
    br = math.radians(heading_deg)
    dlat = (dist * math.cos(br)) / _EARTH_R_M
    dlon = (dist * math.sin(br)) / (_EARTH_R_M * max(1e-9, math.cos(math.radians(lat))))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def fill_gaps(fixes: Sequence[Fix], *, max_gap_s: float = 30.0, step_s: float = 1.0) -> List[Fix]:
    """Insert interpolated fixes across short GPS gaps using last speed/heading.

    Gaps longer than max_gap_s are left unfilled. Synthetic fixes have valid=False.
    """
    if not fixes:
        return []
    ordered = sorted(fixes, key=lambda f: f.ts)
    out: List[Fix] = [ordered[0]]
    for prev, cur in zip(ordered, ordered[1:]):
        gap = cur.ts - prev.ts
        if gap <= step_s or gap > max_gap_s or not prev.valid:
            out.append(cur)
            continue
        t = prev.ts + step_s
        lat, lon = prev.lat, prev.lon
        while t < cur.ts - 1e-9:
            lat, lon = _step(lat, lon, prev.speed_mps, prev.heading_deg, step_s)
            out.append(
                Fix(
                    ts=t,
                    lat=lat,
                    lon=lon,
                    speed_mps=prev.speed_mps,
                    heading_deg=prev.heading_deg,
                    valid=False,
                )
            )
            t += step_s
        out.append(cur)
    return out
