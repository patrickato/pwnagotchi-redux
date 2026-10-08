"""JSON Lines import into the sighting store."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from redux.geo.db import Sighting, SightingStore

PathLike = Union[str, Path]


def import_jsonl(source: Union[PathLike, str], store: SightingStore) -> int:
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).exists()):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = str(source)
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
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
                provenance=str(r.get("provenance", "jsonl import") or "jsonl import"),
            )
        )
        n += 1
    return n
