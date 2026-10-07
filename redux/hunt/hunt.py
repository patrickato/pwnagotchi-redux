"""RSSI-gradient fox-hunt — walk onto a scoped target with the signal you already log.

"Warmer / colder," a coarse proximity band, and — when GPS fixes are present — a
weighted-centroid position estimate with a bearing to walk. No FTM, no extra
hardware; it runs off the same RSSI the sighting store already records.

Honest about what RSSI can and can't do:
  - **Trend (warmer/colder) is robust** because it's relative — rising RSSI over
    recent samples means you're closing in, falling means you're moving away, and
    a dead band in between is reported as "steady," not a false reading.
  - **Distance is a coarse band, never fake meters.** RSSI→range is wildly
    environment-dependent (walls, bodies, multipath), so the hunt reports a band
    ("very close" … "far") with the caveat stated, not a precise distance.
  - **The position estimate reuses the geo weighted-centroid** (which carries its
    own error radius), and the bearing is only offered once there are enough
    geo-tagged samples to form one — otherwise it's None, not a guess.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple

from ..geo.estimate import estimate_from_coords, LocationEstimate


# RSSI (dBm) → coarse proximity band. Rough, environment-dependent — labelled so.
_BANDS = [
    (-50, "on top of it"),
    (-62, "very close"),
    (-72, "close"),
    (-82, "nearby"),
]


def _band(rssi: Optional[int]) -> str:
    if rssi is None:
        return "unknown"
    for threshold, label in _BANDS:
        if rssi >= threshold:
            return label
    return "far"


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compass(bearing: float) -> str:
    pts = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return pts[int((bearing + 22.5) % 360 // 45)]


@dataclass(frozen=True)
class HuntObservation:
    rssi: int
    ts: float = 0.0
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass(frozen=True)
class HuntState:
    target: str
    samples: int
    trend: str                 # warmer | colder | steady | unknown
    last_rssi: Optional[int]
    best_rssi: Optional[int]
    band: str
    estimate: Optional[dict]   # {lat, lon, error_radius_m} or None
    bearing_deg: Optional[float]
    bearing_compass: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class FoxHunt:
    """Tracks the gradient to one target across a stream of RSSI observations."""
    target: str
    window: int = 6            # samples per side when measuring the trend slope
    _rssi: Deque[int] = field(default_factory=deque)
    _located: List[Tuple[float, float, int]] = field(default_factory=list)
    _best: Optional[int] = None

    def __post_init__(self):
        self._rssi = deque(maxlen=self.window * 2)

    def observe(self, obs: HuntObservation) -> HuntState:
        self._rssi.append(int(obs.rssi))
        if self._best is None or obs.rssi > self._best:
            self._best = int(obs.rssi)
        if obs.lat is not None and obs.lon is not None:
            self._located.append((float(obs.lat), float(obs.lon), int(obs.rssi)))

        trend = self._trend()
        est = estimate_from_coords(self._located) if len(self._located) >= 2 else None
        est_d, brg, brg_c = None, None, None
        if est is not None:
            est_d = {"lat": est.lat, "lon": est.lon, "error_radius_m": est.error_radius_m}
            # bearing from the latest observer position to the estimate
            cur_lat, cur_lon, _ = self._located[-1]
            brg = bearing_deg(cur_lat, cur_lon, est.lat, est.lon)
            brg_c = compass(brg)

        band = _band(self._rssi[-1] if self._rssi else None)
        reason = self._reason(trend, band, est)
        return HuntState(
            target=self.target, samples=len(self._rssi), trend=trend,
            last_rssi=self._rssi[-1] if self._rssi else None, best_rssi=self._best,
            band=band, estimate=est_d, bearing_deg=brg, bearing_compass=brg_c, reason=reason)

    def _trend(self) -> str:
        """Responsive recent direction: compare the last k samples to the k before
        them, so a retreat after an approach reads 'colder' promptly rather than
        being masked by the earlier climb."""
        data = list(self._rssi)
        if len(data) < 2:
            return "unknown"
        k = max(1, self.window // 3)
        if len(data) >= 2 * k:
            recent = sum(data[-k:]) / k
            prior = sum(data[-2 * k:-k]) / k
        else:
            recent = data[-1]
            prior = sum(data[:-1]) / (len(data) - 1)
        delta = recent - prior        # positive = rising RSSI = closing in
        if delta > 2.0:
            return "warmer"
        if delta < -2.0:
            return "colder"
        return "steady"

    def _reason(self, trend: str, band: str, est) -> str:
        bits = [f"{trend} ({band})"]
        if est is not None:
            bits.append(f"est. location ±{est.error_radius_m:.0f} m from {est.observation_count} fixes")
        else:
            bits.append("no position estimate yet (needs ≥2 GPS-tagged samples)")
        bits.append("RSSI range is environment-dependent — band is coarse")
        return "; ".join(bits)
