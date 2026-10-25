"""Unwanted-tracker ("is it following me?") analysis over sightings.

The BLE tracker detector (`redux/detect/ble_tracker.py`) answers *what* a device
is — an AirTag/Find My advertiser, a skimmer module. This answers the scarier
question the Apple/Google "unwanted tracking" alerts answer: *is this thing
moving with me?*

A device is a potential **follower** when the SAME identifier is seen
**persistently over time** AND **across multiple distinct locations spanning
real distance** — i.e. you moved and it stayed with you. A device seen once, or
only sitting in one place (a neighbor's tag, a fixed beacon), is explicitly
**not** flagged. Every verdict is glass-box: it says how long, how many places,
and how far.

Pure and testable: it operates on a flat list of sightings (each needs `mac`,
`ts`, and ideally `lat`/`lon`); no store, no radio. Real data only — a device
with no location history can't be shown to have moved with you, so it is
reported as "insufficient location data", never guessed to be following.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .geoutil import haversine_m

# defaults tuned to the Apple/Google unwanted-tracking spirit: a brief, co-located
# encounter is noise; persistence across distance is the signal.
MIN_DURATION_S = 600.0      # seen across at least ~10 minutes
MIN_LOCATIONS = 2           # at least two distinct places
MIN_SPAN_M = 100.0          # those places span at least ~100 m (you actually moved)
MIN_HITS = 3                # at least a few observations


@dataclass(frozen=True)
class FollowerVerdict:
    mac: str
    hits: int
    first_seen: float
    last_seen: float
    duration_s: float
    locations: int          # distinct located points
    span_m: float           # max distance between any two located points
    following: bool
    score: float            # 0..1 confidence it's travelling with you
    reason: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _distinct_points(pts: List[Tuple[float, float]], *, round_m: float = 25.0
                     ) -> List[Tuple[float, float]]:
    """Collapse near-identical fixes (within ~round_m) so GPS jitter at one spot
    doesn't read as many 'locations'."""
    out: List[Tuple[float, float]] = []
    for p in pts:
        if all(haversine_m(p[0], p[1], q[0], q[1]) > round_m for q in out):
            out.append(p)
    return out


def _max_span(pts: List[Tuple[float, float]]) -> float:
    span = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            span = max(span, haversine_m(pts[i][0], pts[i][1], pts[j][0], pts[j][1]))
    return span


def analyze_mac(mac: str, sightings: List, *,
                min_duration_s: float = MIN_DURATION_S,
                min_locations: int = MIN_LOCATIONS,
                min_span_m: float = MIN_SPAN_M,
                min_hits: int = MIN_HITS) -> FollowerVerdict:
    """Follower verdict for one device's sightings (already filtered to one mac)."""
    ts = sorted(float(getattr(s, "ts", 0.0) or 0.0) for s in sightings)
    hits = len(ts)
    first, last = (ts[0], ts[-1]) if ts else (0.0, 0.0)
    duration = last - first
    pts = [(float(s.lat), float(s.lon)) for s in sightings
           if getattr(s, "lat", None) is not None and getattr(s, "lon", None) is not None]
    distinct = _distinct_points(pts)
    span = _max_span(distinct)
    nloc = len(distinct)

    if not pts:
        return FollowerVerdict(mac, hits, first, last, duration, 0, 0.0, False, 0.0,
                               f"{hits} sighting(s) but no location data — can't tell if it moved with you")
    following = (duration >= min_duration_s and nloc >= min_locations
                 and span >= min_span_m and hits >= min_hits)
    # a simple, honest confidence: how many of the four conditions are met
    met = sum([duration >= min_duration_s, nloc >= min_locations,
               span >= min_span_m, hits >= min_hits])
    score = round(met / 4.0, 2)
    if following:
        reason = (f"FOLLOWING: seen {hits}x over {duration/60:.0f} min across {nloc} places "
                  f"spanning {span:.0f} m — it moved with you")
    else:
        bits = []
        if duration < min_duration_s:
            bits.append(f"only {duration/60:.0f} min")
        if nloc < min_locations:
            bits.append(f"{nloc} location(s)")
        if span < min_span_m:
            bits.append(f"{span:.0f} m span (stationary)")
        if hits < min_hits:
            bits.append(f"{hits} hit(s)")
        reason = "not a follower: " + ", ".join(bits)
    return FollowerVerdict(mac, hits, first, last, duration, nloc, span,
                           following, score, reason)


def follower_analysis(sightings: Iterable, **thresholds) -> List[FollowerVerdict]:
    """Group sightings by mac and return verdicts, potential followers first
    (then by score, then by how far they travelled with you)."""
    by_mac: Dict[str, List] = {}
    for s in sightings:
        mac = getattr(s, "mac", None)
        if mac:
            by_mac.setdefault(mac, []).append(s)
    verdicts = [analyze_mac(mac, group, **thresholds) for mac, group in by_mac.items()]
    verdicts.sort(key=lambda v: (v.following, v.score, v.span_m), reverse=True)
    return verdicts


def followers(sightings: Iterable, **thresholds) -> List[FollowerVerdict]:
    """Just the devices flagged as following you."""
    return [v for v in follower_analysis(sightings, **thresholds) if v.following]


@dataclass(frozen=True)
class _Obs:
    """A lightweight located observation (what the analysis consumes)."""
    mac: str
    ts: float
    lat: Optional[float] = None
    lon: Optional[float] = None


class ObservationLog:
    """Append-only JSONL of located observations — the TIME SERIES the follower
    analysis needs (the sighting store collapses to one row per device, so it
    can't show movement). Bounded/pruned; corrupt-line tolerant.

    Observations are tagged with the RECEIVER's position (where you heard the
    device), via an injected position provider — exactly like an ISM sighting.
    """

    def __init__(self, path, *, position=None, max_rows: int = 50000):
        self.path = Path(path)
        self._pos = position          # callable() -> (lat, lon) | None
        self._max = max_rows

    def record(self, mac: str, ts: float, *, lat=None, lon=None) -> None:
        if not mac:
            return
        if (lat is None or lon is None) and callable(self._pos):
            try:
                fix = self._pos()
            except Exception:
                fix = None
            if fix and len(fix) >= 2:
                lat, lon = fix[0], fix[1]
        row = {"mac": mac, "ts": float(ts or 0.0), "lat": lat, "lon": lon}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            pass

    def observe(self, em, *, kinds=("ble",)) -> None:
        """Bus subscriber: log located observations for the given sighting kinds
        (BLE by default — that's where trackers live)."""
        p = getattr(em, "payload", {}) or {}
        ev = p.get("event")
        etype = getattr(ev, "type", "")
        if not any(etype.startswith(k) for k in kinds):
            return
        data = getattr(ev, "data", {}) or {}
        mac = (data.get("mac") or data.get("address") or data.get("bssid") or "")
        if mac:
            self.record(mac, getattr(ev, "at", 0.0) or 0.0)

    def load(self) -> List[_Obs]:
        out: List[_Obs] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(_Obs(mac=d["mac"], ts=float(d.get("ts", 0.0)),
                                lat=d.get("lat"), lon=d.get("lon")))
            except (ValueError, KeyError, TypeError):
                continue
        return out

    def prune(self) -> None:
        rows = self.load()
        if len(rows) <= self._max:
            return
        keep = rows[-self._max:]
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps({"mac": r.mac, "ts": r.ts, "lat": r.lat, "lon": r.lon},
                                   sort_keys=True) + "\n")
        os.replace(tmp, self.path)
