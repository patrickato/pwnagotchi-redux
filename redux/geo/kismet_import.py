"""Import a Kismet SQLite (kismetdb) into the sighting store.

Reads common device/SSID tables when present; skips missing tables.
Pure sqlite3 — no Kismet dependency.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from redux.geo.db import Sighting, SightingStore

PathLike = Union[str, Path]


def import_kismetdb(path: PathLike, store: SightingStore) -> int:
    """Import WiFi devices from a kismetdb file. Returns rows inserted/merged."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    n = 0
    try:
        # Prefer devices table (Kismet modern); fall back to best-effort SELECT
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "devices" in tables:
            rows = conn.execute(
                "SELECT * FROM devices LIMIT 100000"
            ).fetchall()
            for row in rows:
                keys = set(row.keys())
                mac = (row["devmac"] if "devmac" in keys else row["mac"] if "mac" in keys else "") or ""
                if not mac:
                    continue
                ssid = ""
                if "name" in keys and row["name"]:
                    ssid = str(row["name"])
                lat = row["avg_lat"] if "avg_lat" in keys else row["min_lat"] if "min_lat" in keys else None
                lon = row["avg_lon"] if "avg_lon" in keys else row["min_lon"] if "min_lon" in keys else None
                rssi = row["strongest"] if "strongest" in keys else None
                ts = float(row["last_time"] if "last_time" in keys and row["last_time"] else 0.0)
                store.insert(
                    Sighting(
                        kind="wifi",
                        mac=str(mac),
                        ssid=ssid,
                        lat=float(lat) if lat not in (None, 0, 0.0) else None,
                        lon=float(lon) if lon not in (None, 0, 0.0) else None,
                        rssi=int(rssi) if rssi is not None else None,
                        ts=ts,
                        provenance=f"kismetdb import from {Path(path).name}",
                    )
                )
                n += 1
        return n
    finally:
        conn.close()
