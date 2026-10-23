"""Native TFT moving-map painter — own track + AP/BLE pins, real-GPS-only.

Brings the web dashboard's moving map to the 480x320 SPI panel, with no I/O
beyond drawing into a supplied Pillow image. Honest by construction: it plots
ONLY real positions. With no GPS fix and no geo-tagged pins it paints an explicit
empty state, never a decorative fake map — the same stance as the web map and the
trend chart ("not decoded RF proof").

The projection and validation are pure functions, so the whole panel is testable
with no hardware (a fake draw recorder is enough).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

_MAX_TRACK = 512
_MAX_PINS = 256
_MIN_SPAN_DEG = 0.0015  # ~150 m floor so a single fix doesn't zoom to infinity


def _coord(lat, lon) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) only if both are real, finite, in range; else None."""
    if type(lat) not in (int, float) or type(lon) not in (int, float):
        return None
    try:
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return float(lat), float(lon)


@dataclass(frozen=True)
class BBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    @property
    def center(self) -> Tuple[float, float]:
        return (self.min_lat + self.max_lat) / 2.0, (self.min_lon + self.max_lon) / 2.0

    def padded(self) -> "BBox":
        """Enforce a minimum span (so a single point / a tight cluster still maps)
        and add 12% margin so markers aren't flush to the border."""
        lat_span = max(self.max_lat - self.min_lat, _MIN_SPAN_DEG)
        lon_span = max(self.max_lon - self.min_lon, _MIN_SPAN_DEG)
        clat, clon = self.center
        mlat, mlon = lat_span * 0.6, lon_span * 0.6   # half-span * 1.2 margin
        return BBox(clat - mlat, clat + mlat, clon - mlon, clon + mlon)


def bounds(latlons: Sequence[Tuple[float, float]]) -> Optional[BBox]:
    pts = [p for p in (_coord(*c) for c in latlons) if p is not None]
    if not pts:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return BBox(min(lats), max(lats), min(lons), max(lons))


def project(lat: float, lon: float, bbox: BBox,
            region: Tuple[int, int, int, int]) -> Tuple[int, int]:
    """Equirectangular local projection of (lat, lon) to a pixel in `region`
    (left, top, right, bottom). North is up; longitude is compressed by
    cos(center latitude) so the aspect is right at the grid's latitude."""
    left, top, right, bottom = region
    clat, _ = bbox.center
    cos_lat = max(0.01, math.cos(math.radians(clat)))
    lon_w = (bbox.max_lon - bbox.min_lon) * cos_lat or _MIN_SPAN_DEG
    lat_h = (bbox.max_lat - bbox.min_lat) or _MIN_SPAN_DEG
    fx = ((lon - bbox.min_lon) * cos_lat) / lon_w
    fy = (lat - bbox.min_lat) / lat_h
    x = left + fx * (right - left)
    y = bottom - fy * (bottom - top)          # invert: north (max lat) at top
    return (int(round(max(left, min(right, x)))),
            int(round(max(top, min(bottom, y)))))


def map_view(view) -> dict:
    """Validate a raw map view into {own, track, pins} of real coordinates, or
    {"empty": True, "reason": ...} when there is nothing real to plot."""
    if not isinstance(view, dict):
        return {"empty": True, "reason": "no map data"}
    own = _coord(*view["own"]) if isinstance(view.get("own"), (list, tuple)) and len(view["own"]) == 2 else None
    track: List[Tuple[float, float]] = []
    raw_track = view.get("track")
    if isinstance(raw_track, list):
        for c in raw_track[:_MAX_TRACK]:
            if isinstance(c, (list, tuple)) and len(c) == 2:
                p = _coord(c[0], c[1])
                if p is not None:
                    track.append(p)
    pins: List[Tuple[float, float, Optional[int], str]] = []
    raw_pins = view.get("pins")
    if isinstance(raw_pins, list):
        for pin in raw_pins[:_MAX_PINS]:
            if not isinstance(pin, (list, tuple)) or len(pin) < 2:
                continue
            p = _coord(pin[0], pin[1])
            if p is None:
                continue
            rssi = pin[2] if len(pin) >= 3 and type(pin[2]) in (int, float) else None
            kind = pin[3] if len(pin) >= 4 and isinstance(pin[3], str) else "ap"
            pins.append((p[0], p[1], int(rssi) if rssi is not None else None, kind))
    if own is None and not track and not pins:
        return {"empty": True, "reason": "no GPS fix and no geo-tagged sightings yet"}
    return {"empty": False, "own": own, "track": track, "pins": pins}


def build_map_view(sightings, *, own=None, track=None) -> dict:
    """Assemble a map view from SightingStore-like rows (each with .lat/.lon and
    optional .rssi/.kind) plus an optional own position and track. Rows without
    real coordinates are dropped — real GPS only. Returns a dict ready for
    draw_map()/map_view()."""
    pins = []
    for s in sightings or ():
        lat = getattr(s, "lat", None)
        lon = getattr(s, "lon", None)
        if _coord(lat, lon) is None:
            continue
        pins.append((float(lat), float(lon), getattr(s, "rssi", None),
                     "ble" if getattr(s, "kind", "") == "ble" else "ap"))
    return {"own": list(own) if own else None,
            "track": [list(p) for p in (track or [])],
            "pins": pins}


def _pin_color(rssi, strong, mid, weak):
    if rssi is None:
        return weak
    if rssi >= -55:
        return strong
    if rssi >= -75:
        return mid
    return weak


def draw_map(draw, view, *, font, ink, subtle, teal, amber, blue,
             region: Tuple[int, int, int, int] = (14, 190, 466, 300)) -> int:
    """Paint the moving map into `region` on a 480x320 UI. Returns the number of
    plotted features (0 ⇒ the honest empty state was shown)."""
    draw.text((20, 160), "LIVE MAP — REAL GPS ONLY", font=font(11, bold=True), fill=subtle)
    data = map_view(view)
    if data["empty"]:
        draw.text((20, 205), "NO MAP FIX", font=font(16, bold=True), fill=amber)
        draw.text((20, 234), data["reason"], font=font(11), fill=subtle)
        draw.text((20, 270), "A map needs a real position — nothing is invented.",
                  font=font(10), fill=subtle)
        return 0
    all_pts = list(data["track"]) + [(p[0], p[1]) for p in data["pins"]]
    if data["own"] is not None:
        all_pts.append(data["own"])
    bbox = bounds(all_pts).padded()
    left, top, right, bottom = region
    draw.rectangle((left, top, right, bottom), outline=(37, 58, 68), width=1)

    # own track as a polyline
    if len(data["track"]) >= 2:
        coords = [project(la, lo, bbox, region) for la, lo in data["track"]]
        draw.line(coords, fill=teal, width=2)

    # AP / BLE pins coloured by signal strength
    for la, lo, rssi, kind in data["pins"]:
        x, y = project(la, lo, bbox, region)
        color = _pin_color(rssi, amber, teal, subtle)
        if kind == "ble":
            draw.rectangle((x - 2, y - 2, x + 2, y + 2), outline=color, width=1)
        else:
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)

    # own position: a crosshair so it reads distinctly from pins
    if data["own"] is not None:
        x, y = project(data["own"][0], data["own"][1], bbox, region)
        draw.line((x - 5, y, x + 5, y), fill=blue, width=2)
        draw.line((x, y - 5, x, y + 5), fill=blue, width=2)

    plotted = len(data["pins"]) + (1 if data["own"] else 0)
    draw.text((20, 177), f"{len(data['pins'])} PINS · {len(data['track'])} TRACK PTS",
              font=font(10), fill=ink)
    draw.text((left + 4, top + 2), "N↑", font=font(9), fill=subtle)
    return plotted
