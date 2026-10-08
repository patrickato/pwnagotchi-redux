"""JSONL import/export of sightings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from redux.geo.db import Sighting, SightingStore

PathLike = Union[str, Path]


def export_jsonl(store: SightingStore, path: PathLike) -> int:
    lines: List[str] = []
    for s in store.query():
        lines.append(json.dumps({
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
        }))
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def import_jsonl(store: SightingStore, path: PathLike) -> int:
    text = Path(path).read_text(encoding="utf-8")
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        store.insert(Sighting(
            kind=str(r.get("kind", "wifi")),
            mac=str(r.get("mac", "")),
            ssid=str(r.get("ssid", "") or ""),
            lat=r.get("lat"),
            lon=r.get("lon"),
            rssi=r.get("rssi"),
            channel=r.get("channel"),
            source_radio=str(r.get("source_radio", "") or ""),
            ts=float(r.get("ts", 0.0) or 0.0),
            provenance=str(r.get("provenance", "jsonl import") or "jsonl import"),
        ))
        n += 1
    return n
