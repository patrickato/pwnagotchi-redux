"""Direction finding — track a signal to its origin from positioned RSSI.

The honest single-antenna technique ("fox hunting"): one antenna gives signal
*strength*, not bearing, so you move around and the source is where the signal
peaks. From a time series of (rssi, where-you-heard-it) this estimates:

  * trend — are you getting WARMER or COLDER (latest strength vs the recent run)?
  * a best-guess source location — the RSSI-weighted centroid of your positions
    (stronger readings pull it toward where you heard it loudest);
  * a rough compass bearing from where you are now toward that estimate;
  * a confidence that is honest about its limits — readings from ~one spot can't
    triangulate, so it says so instead of inventing precision.

This is a guided-search aid, not GPS-accurate coordinates (that needs a
directional antenna for true bearing, or multiple nodes for multilateration —
both noted as upgrade paths). Pure/deterministic: readings in, guidance out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


@dataclass(frozen=True)
class Reading:
    ts: float
    rssi: int
    lat: float
    lon: float


@dataclass(frozen=True)
class LocateResult:
    target: str
    readings: int
    trend: str                       # warmer | colder | flat | unknown
    strongest_rssi: Optional[int]
    strongest_at: Optional[Tuple[float, float]]
    est_lat: Optional[float]
    est_lon: Optional[float]
    bearing: Optional[str]           # compass point from latest position toward the estimate
    confidence: str
    guidance: str


def _coerce(readings: Sequence) -> List[Reading]:
    out: List[Reading] = []
    for r in readings:
        rssi = getattr(r, "rssi", None) if not isinstance(r, dict) else r.get("rssi")
        lat = getattr(r, "lat", None) if not isinstance(r, dict) else r.get("lat")
        lon = getattr(r, "lon", None) if not isinstance(r, dict) else r.get("lon")
        ts = getattr(r, "ts", 0.0) if not isinstance(r, dict) else r.get("ts", 0.0)
        if rssi is None or lat is None or lon is None:
            continue
        try:
            out.append(Reading(ts=float(ts or 0.0), rssi=int(rssi), lat=float(lat), lon=float(lon)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda r: r.ts)
    return out


def _bearing(from_lat, from_lon, to_lat, to_lon) -> Optional[str]:
    dlat = to_lat - from_lat
    dlon = to_lon - from_lon
    if abs(dlat) < 1e-9 and abs(dlon) < 1e-9:
        return None
    ang = math.degrees(math.atan2(dlon * math.cos(math.radians(from_lat)), dlat)) % 360
    return _COMPASS[int((ang + 22.5) // 45) % 8]


def _spread(rs: Sequence[Reading]) -> float:
    lats = [r.lat for r in rs]
    lons = [r.lon for r in rs]
    return (max(lats) - min(lats)) + (max(lons) - min(lons))


def locate(readings: Sequence, *, target: str = "source", window: int = 3) -> LocateResult:
    """Estimate direction + a best-guess origin from positioned RSSI readings."""
    rs = _coerce(readings)
    if not rs:
        return LocateResult(target, 0, "unknown", None, None, None, None, None,
                            "no positioned readings", "no readings with RSSI + GPS yet")

    strongest = max(rs, key=lambda r: r.rssi)
    # RSSI-weighted centroid (linear power weights): pulled toward loud positions
    wsum = lat_acc = lon_acc = 0.0
    for r in rs:
        w = 10 ** (r.rssi / 10.0)
        wsum += w
        lat_acc += w * r.lat
        lon_acc += w * r.lon
    est_lat, est_lon = (lat_acc / wsum, lon_acc / wsum) if wsum else (None, None)

    # trend: last `window` mean vs the run before it
    trend = "unknown"
    if len(rs) >= 2:
        recent = rs[-window:]
        prior = rs[:-window] or rs[:-1]
        rmean = sum(r.rssi for r in recent) / len(recent)
        pmean = sum(r.rssi for r in prior) / len(prior)
        trend = "warmer" if rmean > pmean + 1 else "colder" if rmean < pmean - 1 else "flat"

    bearing = _bearing(rs[-1].lat, rs[-1].lon, est_lat, est_lon) if est_lat is not None else None

    spread = _spread(rs)
    if len(rs) < 3 or spread < 1e-7:
        confidence = "low — move around to triangulate (readings are from ~one spot)"
    elif len(rs) < 8:
        confidence = "moderate — keep moving toward the warm direction"
    else:
        confidence = "good — estimate is firming up"

    g = [f"strongest {strongest.rssi} dBm at ({strongest.lat:.5f}, {strongest.lon:.5f})"]
    if trend in ("warmer", "colder", "flat"):
        g.append(f"you're getting {trend}")
    if bearing:
        g.append(f"head {bearing} toward (~{est_lat:.5f}, {est_lon:.5f})")
    guidance = "; ".join(g)

    return LocateResult(target=target, readings=len(rs), trend=trend,
                        strongest_rssi=strongest.rssi,
                        strongest_at=(strongest.lat, strongest.lon),
                        est_lat=est_lat, est_lon=est_lon, bearing=bearing,
                        confidence=confidence, guidance=guidance)
