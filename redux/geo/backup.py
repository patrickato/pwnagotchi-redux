"""Sighting-store backup / restore as JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union

from redux.geo.db import Sighting, SightingStore

PathLike = Union[str, Path]


def dump_store(store: SightingStore) -> List[Dict[str, Any]]:
    rows = []
    for s in store.query():
        rows.append({
            "kind": s.kind,
            "mac": s.mac,
            "ssid": s.ssid,
            "lat": s.lat,
            "lon": s.lon,
            "rssi": s.rssi,
            "channel": s.channel,
            "source_radio": s.source_radio,
            "ts": s.ts,
            "first_seen": s.first_seen,
            "provenance": s.provenance,
        })
    return rows


def dump_store_json(store: SightingStore, path: PathLike) -> int:
    data = dump_store(store)
    Path(path).write_text(json.dumps({"sightings": data}, indent=2), encoding="utf-8")
    return len(data)


def restore_store(store: SightingStore, rows: List[Dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        store.insert(
            Sighting(
                kind=str(r.get("kind", "wifi")),
                mac=str(r.get("mac", "")),
                ssid=str(r.get("ssid", "") or ""),
                lat=r.get("lat"),
                lon=r.get("lon"),
                rssi=r.get("rssi"),
                channel=r.get("channel"),
                source_radio=str(r.get("source_radio", "") or ""),
                ts=float(r.get("ts", 0.0) or 0.0),
                provenance=str(r.get("provenance", "restore") or "restore"),
            )
        )
        n += 1
    return n


def restore_store_json(store: SightingStore, path: PathLike) -> int:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw["sightings"] if isinstance(raw, dict) and "sightings" in raw else raw
    return restore_store(store, list(rows))
