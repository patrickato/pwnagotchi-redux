"""Expedition session + Wrapped summary.

The log persists sessions as JSON (atomic write, SD-friendly). `wrapped()`
computes the recap from the sightings whose timestamps fall in the session
window — counts by kind, discoveries first-seen during the session, unique
vendors, located count, and duration. No distance is invented: we only have a
receiver position per sighting, not a continuous GPS track, so we report what
was actually recorded rather than a made-up mileage.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import List, Optional


def _oui(mac: str) -> str:
    parts = (mac or "").lower().split(":")
    return ":".join(parts[:3]) if len(parts) >= 3 else ""


@dataclass(frozen=True)
class Expedition:
    name: str
    started_at: float
    ended_at: Optional[float] = None

    @property
    def active(self) -> bool:
        return self.ended_at is None

    def duration(self, now: Optional[float] = None) -> float:
        end = self.ended_at if self.ended_at is not None else (now if now is not None else time.time())
        return max(0.0, end - self.started_at)


@dataclass
class ExpeditionLog:
    expeditions: List[Expedition]
    path: Optional[str] = None

    def __init__(self, expeditions: Optional[List[Expedition]] = None, path: Optional[str] = None):
        self.expeditions = list(expeditions or [])
        self.path = path

    def current(self) -> Optional[Expedition]:
        for e in reversed(self.expeditions):
            if e.active:
                return e
        return None

    def start(self, name: str, now: Optional[float] = None) -> Expedition:
        now = time.time() if now is None else now
        cur = self.current()
        if cur is not None:                       # auto-close an open one; one at a time
            self._replace(cur, Expedition(cur.name, cur.started_at, now))
        exp = Expedition(name, now, None)
        self.expeditions.append(exp)
        return exp

    def end(self, now: Optional[float] = None) -> Optional[Expedition]:
        now = time.time() if now is None else now
        cur = self.current()
        if cur is None:
            return None
        ended = Expedition(cur.name, cur.started_at, now)
        self._replace(cur, ended)
        return ended

    def _replace(self, old: Expedition, new: Expedition) -> None:
        self.expeditions = [new if e is old else e for e in self.expeditions]

    # --- persistence ------------------------------------------------------- #

    def save(self, path: Optional[str] = None) -> str:
        p = path or self.path
        if not p:
            raise ValueError("no path to save ExpeditionLog to")
        tmp = f"{p}.tmp"
        with open(tmp, "w") as f:
            json.dump({"version": 1, "expeditions": [asdict(e) for e in self.expeditions]},
                      f, indent=2, sort_keys=True)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
        self.path = p
        return p

    @classmethod
    def load(cls, path: str) -> "ExpeditionLog":
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return cls([], path)
        exps = []
        for d in data.get("expeditions", []):
            try:
                exps.append(Expedition(d["name"], float(d["started_at"]),
                                       None if d.get("ended_at") is None else float(d["ended_at"])))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(exps, path)


def wrapped(store, expedition: Expedition, *, now: Optional[float] = None) -> dict:
    """The glance recap for one expedition, computed from the sightings in its
    time window."""
    now = time.time() if now is None else now
    start = expedition.started_at
    end = expedition.ended_at if expedition.ended_at is not None else now
    rows = store.query(since=start, until=end) if hasattr(store, "query") else []

    by_kind: dict = {}
    vendors = set()
    located = 0
    discoveries = 0
    for s in rows:
        by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
        o = _oui(s.mac)
        if o:
            vendors.add(o)
        if s.lat is not None and s.lon is not None:
            located += 1
        fs = s.first_seen if s.first_seen is not None else s.ts
        if start <= fs <= end:
            discoveries += 1

    dur = expedition.duration(now=now)
    hrs, rem = divmod(int(dur), 3600)
    mins = rem // 60
    headline = (f"{expedition.name}: {hrs}h{mins:02d}m · {len(rows)} sightings"
                f" · {discoveries} new · {len(vendors)} vendors"
                + (f" · {located} located" if located else ""))
    return {
        "name": expedition.name,
        "active": expedition.active,
        "duration_s": dur,
        "total": len(rows),
        "by_kind": by_kind,
        "discoveries": discoveries,          # first-seen during this session
        "unique_vendors": len(vendors),
        "located": located,
        "headline": headline,
    }
