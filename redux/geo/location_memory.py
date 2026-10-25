"""Temporal RF diff — what changed at a location since your last visit.

The sighting store keeps one row per device (its last-seen `ts` + `first_seen`),
not a per-visit history, so it can't by itself answer "what's different here
since last time." This module snapshots the SET of devices seen at a location
each visit and diffs the current set against the previous snapshot:

  * NEW      — devices seen here now that weren't here last visit
  * GONE     — devices that were here last visit but aren't now
  * CHANGED  — devices in both whose channel hopped or RSSI shifted a lot

A "location" is a geohash cell (from a GPS fix) or a manual label (so it works
indoors / without GPS too). Glass-box: every line carries its reason. This is
counter-surveillance in a box — "that BLE tracker is back", "this AP moved to
ch11" — built entirely from sightings the device already recorded.

Pure/deterministic and fail-safe: an unreadable/again corrupt memory file is
treated as "first visit" (no crash, nothing fabricated).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .db import SightingStore
from .geohash import encode as geohash_encode
from .reid import reidentify

DEFAULT_MEMORY_PATH = "/captures/live/location_memory.json"
Key = Tuple[str, str]        # (kind, mac)


def build_entity_map(clients: list) -> Dict[str, str]:
    """From harvested probe PNLs (ProbeHarvester.clients()), group randomized MACs
    that are one physical device (via redux.geo.reid) into a mac->entity-id map.
    Only MACs that reid links to at least one other get an id; everything else is
    left alone. Used to make the temporal diff see one returning device instead of
    a GONE+NEW pair when it rotates its MAC."""
    out: Dict[str, str] = {}
    for ent in reidentify(clients or []):
        eid = "entity:" + ent.macs[0]          # stable: first (sorted) MAC in the group
        for mac in ent.macs:
            out[mac.lower()] = eid
    return out


@dataclass(frozen=True)
class DeviceChange:
    kind: str
    mac: str
    ssid: str
    was: str                 # prior "ch6 / -40dBm"
    now: str                 # current "ch11 / -38dBm"
    why: str                 # "channel 6 -> 11", "RSSI -40 -> -72"


@dataclass(frozen=True)
class DeviceHere:
    kind: str
    mac: str
    ssid: str
    channel: Optional[int]
    rssi: Optional[int]

    def label(self) -> str:
        bits = [self.kind, self.ssid or self.mac]
        if self.channel is not None:
            bits.append(f"ch{self.channel}")
        if self.rssi is not None:
            bits.append(f"{self.rssi}dBm")
        return " ".join(bits)


@dataclass(frozen=True)
class RFDiff:
    location: str
    first_visit: bool
    last_visit_ts: Optional[float]
    new: Tuple[DeviceHere, ...] = ()
    gone: Tuple[DeviceHere, ...] = ()
    changed: Tuple[DeviceChange, ...] = ()
    here_count: int = 0
    summary: str = ""


def location_key(*, lat: Optional[float] = None, lon: Optional[float] = None,
                 label: Optional[str] = None, precision: int = 7) -> Optional[str]:
    """A stable key for 'this place': a manual label wins; else a geohash cell
    from a GPS fix. None when neither is available (caller must ask for one)."""
    if label:
        clean = "".join(c for c in label.strip() if c.isprintable())[:48]
        return f"label:{clean}" if clean else None
    if lat is None or lon is None:
        return None
    try:
        return "geo:" + geohash_encode(float(lat), float(lon), precision)
    except (TypeError, ValueError):
        return None


def _in_cell(sighting, cell_hash: str, precision: int) -> bool:
    if sighting.lat is None or sighting.lon is None:
        return False
    try:
        return geohash_encode(float(sighting.lat), float(sighting.lon), precision) == cell_hash
    except (TypeError, ValueError):
        return False


def devices_here(store: SightingStore, key: str, *, precision: int = 7,
                 since: Optional[float] = None) -> Dict[Key, DeviceHere]:
    """The set of devices this store has recorded AT `key`. For a geohash key,
    that's every sighting whose own coordinates fall in the cell; for a manual
    label there is no coordinate filter (everything currently in the store),
    optionally bounded to sightings seen since `since`."""
    out: Dict[Key, DeviceHere] = {}
    cell = key[4:] if key.startswith("geo:") else None
    for s in store.query(since=since):
        if cell is not None and not _in_cell(s, cell, precision):
            continue
        out[(s.kind, s.mac)] = DeviceHere(
            kind=s.kind, mac=s.mac, ssid=s.ssid or "",
            channel=s.channel, rssi=s.rssi)
    return out


class LocationMemory:
    """Per-location snapshot store (one JSON file; last snapshot per key)."""

    def __init__(self, path: str = DEFAULT_MEMORY_PATH):
        self.path = path
        self._data: Dict[str, dict] = {}
        try:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
            if isinstance(blob, dict):
                self._data = {k: v for k, v in blob.items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError):
            self._data = {}          # fail-safe: first visit everywhere

    def last(self, key: str) -> Optional[dict]:
        rec = self._data.get(key)
        return rec if isinstance(rec, dict) and isinstance(rec.get("devices"), dict) else None

    def save(self, key: str, devices: Dict[Key, DeviceHere], *, ts: float) -> None:
        self._data[key] = {
            "ts": ts,
            "devices": {f"{k}|{m}": {"ssid": d.ssid, "channel": d.channel, "rssi": d.rssi}
                        for (k, m), d in devices.items()},
        }
        self._flush()

    def _flush(self) -> None:
        try:
            d = os.path.dirname(self.path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".locmem-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            pass                      # best-effort; a failed save just means next visit re-snapshots


def _rssi_moved(a: Optional[int], b: Optional[int], delta: int) -> bool:
    return a is not None and b is not None and abs(int(a) - int(b)) >= delta


def rf_diff(store: SightingStore, memory: LocationMemory, *, key: str,
            precision: int = 7, rssi_delta: int = 18, since: Optional[float] = None,
            now: Optional[float] = None, save: bool = True,
            entities: Optional[Dict[str, str]] = None) -> RFDiff:
    """Diff the devices seen at `key` now against the last snapshot, then (unless
    save=False) record this visit as the new snapshot.

    `entities` (mac->entity-id, from build_entity_map) collapses the randomized
    MACs of one physical device so a device that returns under a fresh MAC is
    recognised as the same device, not a GONE+NEW pair. The snapshot is still
    stored by raw MAC, so resolution uses whatever grouping is known at diff
    time; with no map, behaviour is unchanged."""
    now = time.time() if now is None else now
    current = devices_here(store, key, precision=precision, since=since)
    prior = memory.last(key)
    if prior is None:
        if save:
            memory.save(key, current, ts=now)
        return RFDiff(location=key, first_visit=True, last_visit_ts=None,
                      here_count=len(current),
                      summary=f"first visit to {key} — {len(current)} device(s) recorded as the baseline")

    def _resolve(kind: str, mac: str):
        if entities and kind == "wifi":
            eid = entities.get((mac or "").lower())
            if eid:
                return ("entity", eid)
        return (kind, mac)

    # current: one representative DeviceHere per resolved key (sorted → stable)
    cur_map: Dict[tuple, DeviceHere] = {}
    for (k, m) in sorted(current):
        cur_map.setdefault(_resolve(k, m), current[(k, m)])

    prior_devices = prior.get("devices", {})
    prior_attr: Dict[tuple, dict] = {}
    prior_rep: Dict[tuple, Key] = {}
    for comp in sorted(prior_devices):
        attr = prior_devices[comp] if isinstance(prior_devices[comp], dict) else {}
        kind, _, mac = comp.partition("|")
        rk = _resolve(kind, mac)
        if rk not in prior_attr:
            prior_attr[rk] = attr
            prior_rep[rk] = (kind, mac)

    cur_keys = set(cur_map)
    prior_keys = set(prior_attr)
    new = tuple(cur_map[k] for k in sorted(cur_keys - prior_keys))
    gone = tuple(DeviceHere(kind=prior_rep[k][0], mac=prior_rep[k][1],
                            ssid=str(prior_attr[k].get("ssid", "")),
                            channel=prior_attr[k].get("channel"),
                            rssi=prior_attr[k].get("rssi"))
                 for k in sorted(prior_keys - cur_keys))
    changed: List[DeviceChange] = []
    for k in sorted(cur_keys & prior_keys):
        cur = cur_map[k]
        old = prior_attr.get(k, {})
        reasons = []
        if cur.channel is not None and old.get("channel") is not None and cur.channel != old["channel"]:
            reasons.append(f"channel {old['channel']} -> {cur.channel}")
        if _rssi_moved(cur.rssi, old.get("rssi"), rssi_delta):
            reasons.append(f"RSSI {old.get('rssi')} -> {cur.rssi}")
        if reasons:
            changed.append(DeviceChange(
                kind=cur.kind, mac=cur.mac, ssid=cur.ssid,
                was=f"ch{old.get('channel')} / {old.get('rssi')}dBm",
                now=f"ch{cur.channel} / {cur.rssi}dBm",
                why=", ".join(reasons)))

    last_ts = prior.get("ts") if isinstance(prior.get("ts"), (int, float)) else None
    if save:
        memory.save(key, current, ts=now)        # snapshot stays RAW (by mac)
    ago = ""
    if last_ts is not None:
        secs = max(0, int(now - last_ts))
        ago = f" (last visit {secs // 86400}d {secs % 86400 // 3600}h ago)"
    summary = (f"{key}{ago}: +{len(new)} new, -{len(gone)} gone, "
               f"{len(changed)} changed, {len(current)} here now")
    return RFDiff(location=key, first_visit=False, last_visit_ts=last_ts,
                  new=new, gone=gone, changed=tuple(changed),
                  here_count=len(current), summary=summary)
