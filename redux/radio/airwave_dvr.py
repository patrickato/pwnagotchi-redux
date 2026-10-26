"""Airwave DVR — scrub back through RF history.

The sightings store keeps the BEST view of each device (one deduped row). The DVR
keeps the opposite: an append-only timeline of what was on the air, moment by
moment, so you can rewind and ask "what was here at 3:42pm?" — the AP that
beaconed for ten minutes and vanished, the handshake that flew by, the deauth
burst, the device that probed once and left.

A + B ⇒ C: A (every event the tap / engine / sweep already sees) + B (its
timestamp) ⇒ C (a scrubbable spectrum recorder — a timeline bar, a snapshot at
any instant, a replay of any window). Feeds and is fed by the passive PMKID
sweep, captap and the entity graph.

Bounded + fail-safe: a ring capped by count and age so it can't grow without
limit on the Pi; optional JSONL persistence that survives reboot (the DVR is
part of the brain that persists on /captures, not something Quiet mode wipes).
Pure/deterministic over an injected clock.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


def band_of_channel(ch: Optional[int]) -> str:
    """Coarse band label from a channel number (glass-box, best-effort)."""
    if ch is None:
        return "?"
    try:
        c = int(ch)
    except (TypeError, ValueError):
        return "?"
    if 1 <= c <= 14:
        return "2.4"
    if 32 <= c <= 177:
        return "5"
    return "6" if c >= 1 else "?"


@dataclass(frozen=True)
class AirwaveEvent:
    """One thing heard on the air at one instant."""
    ts: float
    kind: str = "beacon"           # beacon|probe_req|pmkid|eapol|deauth|disassoc|ble|…
    bssid: str = ""
    ssid: str = ""
    channel: Optional[int] = None
    rssi: Optional[int] = None

    @property
    def band(self) -> str:
        return band_of_channel(self.channel)

    def to_dict(self) -> dict:
        return {"ts": self.ts, "kind": self.kind, "bssid": self.bssid, "ssid": self.ssid,
                "channel": self.channel, "rssi": self.rssi, "band": self.band}

    @classmethod
    def from_dict(cls, d: dict) -> "AirwaveEvent":
        return cls(ts=float(d.get("ts", 0.0)), kind=(d.get("kind") or "beacon"),
                   bssid=(d.get("bssid") or "").lower(), ssid=d.get("ssid") or "",
                   channel=d.get("channel"), rssi=d.get("rssi"))


@dataclass
class AirwaveDVR:
    """An append-only, bounded RF timeline you can scrub, snapshot and replay."""
    max_events: int = 50000
    max_age_seconds: float = 86400.0        # keep a day by default
    path: Optional[str] = None              # JSONL persistence (optional)
    _events: List[AirwaveEvent] = field(default_factory=list)

    # ---- ingest -------------------------------------------------------------
    def record(self, event: AirwaveEvent, *, now: Optional[float] = None) -> None:
        self._events.append(event)
        self._trim(now if now is not None else event.ts)
        if self.path:
            self._append_jsonl(event)

    def record_many(self, events: Sequence[AirwaveEvent]) -> None:
        for e in events:
            self._events.append(e)
        if events:
            self._trim(max(e.ts for e in events))
            if self.path:
                for e in events:
                    self._append_jsonl(e)

    def _trim(self, now: float) -> None:
        horizon = now - self.max_age_seconds
        if horizon > 0:
            self._events = [e for e in self._events if e.ts >= horizon]
        if len(self._events) > self.max_events:
            self._events = self._events[-self.max_events:]

    # ---- views --------------------------------------------------------------
    def span(self) -> tuple:
        """(earliest_ts, latest_ts) covered, or (None, None) if empty."""
        if not self._events:
            return (None, None)
        ts = [e.ts for e in self._events]
        return (min(ts), max(ts))

    def window(self, start: float, end: float) -> List[AirwaveEvent]:
        """Every event in [start, end], time-ordered — replay a slice."""
        return sorted((e for e in self._events if start <= e.ts <= end), key=lambda e: e.ts)

    def scrub(self, at: float, *, radius: float = 5.0) -> dict:
        """Snapshot of the air around instant `at` (±radius seconds): which APs,
        devices, channels and event kinds were active."""
        evs = self.window(at - radius, at + radius)
        aps = {e.bssid for e in evs if e.bssid}
        ssids = {e.ssid for e in evs if e.ssid}
        channels = sorted({e.channel for e in evs if e.channel is not None})
        kinds: Dict[str, int] = {}
        for e in evs:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        return {"at": at, "radius": radius, "events": len(evs),
                "aps": sorted(aps), "ssids": sorted(ssids), "channels": channels,
                "bands": sorted({band_of_channel(c) for c in channels}),
                "kinds": kinds}

    def timeline(self, bucket_seconds: float = 60.0) -> List[dict]:
        """Activity histogram over time — the scrub bar. One row per bucket with
        the event count and the busiest kind, so a UI can draw where the action was."""
        if not self._events or bucket_seconds <= 0:
            return []
        lo, hi = self.span()
        out: List[dict] = []
        b = lo
        while b <= hi:
            evs = [e for e in self._events if b <= e.ts < b + bucket_seconds]
            if evs:
                kinds: Dict[str, int] = {}
                for e in evs:
                    kinds[e.kind] = kinds.get(e.kind, 0) + 1
                top = max(kinds.items(), key=lambda kv: kv[1])[0]
                out.append({"t": b, "count": len(evs), "top_kind": top,
                            "aps": len({e.bssid for e in evs if e.bssid})})
            b += bucket_seconds
        return out

    def track(self, bssid: str) -> List[AirwaveEvent]:
        """Every time one BSSID appeared, oldest first — its presence history."""
        b = (bssid or "").lower()
        return sorted((e for e in self._events if e.bssid == b), key=lambda e: e.ts)

    def summary(self) -> dict:
        lo, hi = self.span()
        kinds: Dict[str, int] = {}
        for e in self._events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        return {
            "events": len(self._events),
            "span_seconds": round((hi - lo), 1) if lo is not None else 0.0,
            "earliest": lo, "latest": hi,
            "unique_aps": len({e.bssid for e in self._events if e.bssid}),
            "kinds": kinds,
            "note": (f"{len(self._events)} event(s) over "
                     f"{round((hi - lo) / 60, 1)}m of airtime" if lo is not None
                     else "no RF history recorded yet"),
        }

    # ---- persistence (fail-safe) -------------------------------------------
    def _append_jsonl(self, event: AirwaveEvent) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(event.to_dict()) + "\n")
        except Exception:
            pass

    def load(self) -> int:
        """Reload the timeline from the JSONL file (survives reboot). Returns the
        number of events loaded; fail-safe → 0."""
        if not self.path:
            return 0
        loaded = 0
        try:
            with open(self.path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._events.append(AirwaveEvent.from_dict(json.loads(line)))
                        loaded += 1
                    except (ValueError, TypeError):
                        continue
        except OSError:
            return 0
        if self._events:
            self._trim(max(e.ts for e in self._events))
        return loaded
