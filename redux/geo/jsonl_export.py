"""JSON Lines export of sightings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional, Union

from redux.geo.db import Sighting, SightingStore

PathLike = Union[str, Path]


def sighting_to_obj(s: Sighting) -> dict:
    return {
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
    }


def to_jsonl(sightings: Iterable[Sighting]) -> str:
    return "".join(json.dumps(sighting_to_obj(s)) + "\n" for s in sightings)


def export_store_jsonl(store: SightingStore, path: PathLike, *, kind: Optional[str] = None) -> int:
    rows = store.query(kind=kind) if kind else store.query()
    text = to_jsonl(rows)
    Path(path).write_text(text, encoding="utf-8")
    return len(rows)
