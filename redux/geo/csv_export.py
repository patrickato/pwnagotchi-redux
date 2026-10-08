"""Generic CSV export of sightings (not WiGLE-specific)."""
from __future__ import annotations

import csv
import io
from typing import Iterable, List, Optional

from redux.geo.db import Sighting, SightingStore

_FIELDS = [
    "kind", "mac", "ssid", "lat", "lon", "rssi", "channel",
    "source_radio", "ts", "first_seen", "provenance",
]


def sightings_to_csv(sightings: Iterable[Sighting]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_FIELDS)
    w.writeheader()
    for s in sightings:
        w.writerow({
            "kind": s.kind,
            "mac": s.mac,
            "ssid": s.ssid,
            "lat": s.lat if s.lat is not None else "",
            "lon": s.lon if s.lon is not None else "",
            "rssi": s.rssi if s.rssi is not None else "",
            "channel": s.channel if s.channel is not None else "",
            "source_radio": s.source_radio,
            "ts": s.ts,
            "first_seen": s.first_seen if s.first_seen is not None else "",
            "provenance": s.provenance,
        })
    return buf.getvalue()


def export_store_csv(store: SightingStore, *, kind: Optional[str] = None) -> str:
    rows = store.query(kind=kind) if kind else store.query()
    return sightings_to_csv(rows)
