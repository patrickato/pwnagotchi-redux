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
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
        else:
            text = str(source)
        f = io.StringIO(text)
    else:
        f = source

    # Skip WiGLE metadata lines starting with WigleWifi-
    lines = []
    for line in f:
        if line.startswith("WigleWifi-") or line.startswith("WigleWifi"):
            continue
        lines.append(line)
    reader = csv.DictReader(io.StringIO("".join(lines)))
    n = 0
    for row in reader:
        # Common column names in WigleWifi-1.6
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
