"""Ghost — record a session and replay a *sanitized* copy of it.

Two jobs, one honesty principle (never leak real recon data when you demo or
share your rig):

  - **GhostRecorder** attaches to the bus and captures the live event stream to
    the same JSON schema `redux run/web --replay` already consumes, so a real
    outing can be re-run later.
  - **sanitize_events** produces a *ghost* of a recording: real device identities
    are replaced with stable pseudonyms and location is dropped, while timing,
    structure, RSSI and channel are preserved so the replay still behaves exactly
    like the real session. You can screen-record the dashboard, share a capture
    for review, or demo the rig without exposing a single real MAC, SSID, or
    coordinate.

The mapping is **deterministic** (same input + seed → same ghost), so a shared
ghost is reproducible and a before/after diff is stable — but it is one-way: the
ghost carries no table back to the originals. OUIs are preserved by default
(vendor is not identifying and keeps rarity/vendor demos realistic); pass
``keep_oui=False`` to synthesize locally-administered addresses instead.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Keys in an event's `data` that carry a device identity or location.
_MAC_KEYS = ("mac", "bssid", "address", "bd_address", "ap", "station", "from", "to")
_NAME_KEYS = ("essid", "ssid", "hostname", "name", "local_name")
_LOC_KEYS = ("lat", "lon", "latitude", "longitude", "alt", "altitude", "gps", "location")

_HEX = "0123456789abcdef"


def _looks_like_mac(v: str) -> bool:
    mac = (v or "").strip().replace("-", ":")
    parts = mac.split(":")
    return len(parts) == 6 and all(len(p) == 2 and all(c in _HEX for c in p.lower()) for p in parts)


@dataclass
class GhostMapper:
    """Stable, one-way pseudonymizer. Holds the within-session maps so the same
    real identity always becomes the same ghost — and nothing else."""
    seed: str = "redux-ghost"
    keep_oui: bool = True
    _macs: Dict[str, str] = field(default_factory=dict)
    _names: Dict[str, str] = field(default_factory=dict)

    def _digest(self, s: str) -> bytes:
        return hashlib.sha256(f"{self.seed}:{s}".encode()).digest()

    def mac(self, real: str) -> str:
        key = (real or "").strip().lower()
        if key in self._macs:
            return self._macs[key]
        h = self._digest("mac:" + key)
        if self.keep_oui and _looks_like_mac(key):
            oui = key.replace("-", ":").split(":")[:3]
            tail = [f"{h[i]:02x}" for i in range(3)]
            ghost = ":".join(oui + tail)
        else:
            first = (h[0] | 0x02) & ~0x01  # locally-administered, unicast → clearly synthetic
            ghost = ":".join(f"{o:02x}" for o in [first, h[1], h[2], h[3], h[4], h[5]])
        self._macs[key] = ghost
        return ghost

    def name(self, real: str, kind: str = "net") -> str:
        if not real:
            return real          # preserve empty / hidden SSIDs as-is
        if real in self._names:
            return self._names[real]
        h = self._digest("name:" + real).hex()[:4]
        ghost = f"ghost-{kind}-{h}"
        self._names[real] = ghost
        return ghost

    def stats(self) -> dict:
        """Glass-box: how much was pseudonymized — never the originals."""
        return {"unique_macs": len(self._macs), "unique_names": len(self._names)}


def _sanitize_data(data: dict, mapper: GhostMapper, kind: str) -> dict:
    out = {}
    for k, v in (data or {}).items():
        kl = k.lower()
        if kl in _LOC_KEYS:
            continue                             # drop location entirely
        if kl in _MAC_KEYS and isinstance(v, str) and _looks_like_mac(v):
            out[k] = mapper.mac(v)
        elif kl in _NAME_KEYS and isinstance(v, str):
            out[k] = mapper.name(v, kind=kind)
        else:
            out[k] = v
    return out


def _kind_for_tag(tag: str) -> str:
    t = (tag or "").lower()
    if "ble" in t:
        return "ble"
    if "client" in t:
        return "sta"
    return "ap"


def sanitize_events(events: List[dict], *, seed: str = "redux-ghost",
                    keep_oui: bool = True,
                    mapper: Optional[GhostMapper] = None) -> List[dict]:
    """Return a ghosted copy of a recorded event list: identities pseudonymized,
    location removed, everything else (tag, time, rssi, channel, …) preserved."""
    m = mapper or GhostMapper(seed=seed, keep_oui=keep_oui)
    ghosted: List[dict] = []
    for ev in events or []:
        tag = ev.get("tag", "")
        ghosted.append({
            **{k: v for k, v in ev.items() if k != "data"},
            "data": _sanitize_data(ev.get("data", {}) or {}, m, _kind_for_tag(tag)),
        })
    return ghosted


def sanitize_file(in_path: str, out_path: str, *, seed: str = "redux-ghost",
                  keep_oui: bool = True) -> dict:
    """Ghost a recording file → file. Returns the mapper stats (what was changed)."""
    with open(in_path) as f:
        events = json.load(f)
    m = GhostMapper(seed=seed, keep_oui=keep_oui)
    ghosted = sanitize_events(events, mapper=m)
    tmp = f"{out_path}.tmp"
    with open(tmp, "w") as f:
        json.dump(ghosted, f, indent=2)
    import os
    os.replace(tmp, out_path)
    return {"events": len(ghosted), **m.stats()}


@dataclass
class GhostRecorder:
    """Capture the live event stream to the replay JSON schema, for later re-run.

    Attach to a Augur's bus; it records each normalized event as
    `{"tag", "time", "data"}`. `dump()` writes them (optionally ghosted first, so
    a shared recording never leaks real identities)."""
    _events: List[dict] = field(default_factory=list)

    def attach(self, bus) -> "GhostRecorder":
        from redux.core.signals import Signal
        bus.on(Signal.EVENT, self._capture)
        return self

    def _capture(self, em) -> None:
        ev = em.payload.get("event")
        if ev is None:
            return
        self._events.append({
            "tag": getattr(ev, "raw_tag", "") or getattr(ev, "type", ""),
            "time": float(getattr(ev, "at", 0.0) or 0.0),
            "data": dict(getattr(ev, "data", {}) or {}),
        })

    def events(self) -> List[dict]:
        return list(self._events)

    def dump(self, path: str, *, sanitize: bool = True, seed: str = "redux-ghost",
             keep_oui: bool = True) -> dict:
        events = sanitize_events(self._events, seed=seed, keep_oui=keep_oui) if sanitize \
            else list(self._events)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(events, f, indent=2)
        import os
        os.replace(tmp, path)
        return {"events": len(events), "sanitized": sanitize}
