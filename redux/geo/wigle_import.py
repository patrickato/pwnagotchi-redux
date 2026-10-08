"""WiGLE WigleWifi-1.6 CSV import into the sighting store."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import TextIO, Union

from redux.geo.db import Sighting, SightingStore

PathLike = Union[str, Path]


def _parse_float(v: str):
    try:
        return float(v) if v not in ("", "null", "None") else None
    except (TypeError, ValueError):
        return None


def _parse_int(v: str):
    try:
        return int(float(v)) if v not in ("", "null", "None") else None
    except (TypeError, ValueError):
        return None


def import_wigle_csv(source: Union[PathLike, TextIO, str], store: SightingStore) -> int:
    """Import WiGLE CSV rows into store. Returns rows processed."""
    if hasattr(source, "read"):
        f = source  # type: ignore[assignment]
    elif isinstance(source, Path):
        f = io.StringIO(source.read_text(encoding="utf-8", errors="replace"))
    elif isinstance(source, str):
        # Multiline / has commas → treat as CSV body; else path if it exists
        if "\n" in source or source.count(",") > 3:
            f = io.StringIO(source)
        else:
            p = Path(source)
            if p.exists():
                f = io.StringIO(p.read_text(encoding="utf-8", errors="replace"))
            else:
                f = io.StringIO(source)
    else:
        f = io.StringIO(str(source))

    lines = []
    for line in f:
        if line.startswith("WigleWifi-") or line.startswith("WigleWifi"):
            continue
        lines.append(line)
    reader = csv.DictReader(io.StringIO("".join(lines)))
    n = 0
    for row in reader:
        mac = (row.get("MAC") or row.get("mac") or "").strip()
        if not mac:
            continue
        ssid = (row.get("SSID") or row.get("ssid") or "") or ""
        lat = _parse_float(row.get("CurrentLatitude") or row.get("Latitude") or row.get("lat") or "")
        lon = _parse_float(row.get("CurrentLongitude") or row.get("Longitude") or row.get("lon") or "")
        rssi = _parse_int(row.get("RSSI") or row.get("rssi") or "")
        ch = _parse_int(row.get("Channel") or row.get("channel") or "")
        kind = "wifi"
        typ = (row.get("Type") or "").lower()
        if typ in ("ble", "bt", "bluetooth"):
            kind = "ble"
        store.insert(
            Sighting(
                kind=kind,
                mac=mac,
                ssid=ssid,
                lat=lat,
                lon=lon,
                rssi=rssi,
                channel=ch,
                ts=0.0,
                provenance="wigle csv import",
            )
        )
        n += 1
    return n
