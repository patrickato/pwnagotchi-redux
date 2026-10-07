"""Parse SDR-tool output into geo-tagged Sightings (batched, SD-friendly).

Two source formats, both line/dict JSON records:
  - rtl_433 ISM devices carry a model + id and sensor fields, but no location of
    their own — they are tagged with the RECEIVER's position (where you heard it),
    exactly like a WiFi/BLE sighting.
  - ADS-B aircraft broadcast their OWN lat/lon, so that position is used directly.

Records are converted, then written with one batched insert_many (not per record).
"""
from __future__ import annotations

import calendar
import re
import time
from typing import Iterable, List, Optional, Tuple

from redux.geo import Sighting

_SANITIZE = re.compile(r"[^A-Za-z0-9._-]+")


def _epoch(value) -> float:
    """Parse an rtl_433 'time' string (UTC) to epoch, else 0.0 (never guessed)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return float(calendar.timegm(time.strptime(value[:19], fmt)))
            except ValueError:
                continue
    return 0.0


def rtl433_to_sighting(record: dict, *, position: Optional[Tuple[float, float]] = None) -> Optional[Sighting]:
    """One rtl_433 JSON record → an ISM Sighting, or None if it has no id."""
    model = str(record.get("model") or "").strip()
    ident = record.get("id")
    if ident is None:
        ident = record.get("sensor_id") or record.get("device")
    if not model and ident is None:
        return None
    key = _SANITIZE.sub("-", f"{model}_{ident}").strip("-").lower()
    if not key:
        return None
    lat = lon = None
    if position:
        lat, lon = position[0], position[1]
    ch = record.get("channel")
    try:
        ch = int(ch) if ch is not None else None
    except (TypeError, ValueError):
        ch = None
    return Sighting(
        kind="ism", mac=key, ssid=model,
        lat=lat, lon=lon, channel=ch,
        ts=_epoch(record.get("time")),
        provenance="rtl_433",
    )


def adsb_to_sighting(record: dict) -> Optional[Sighting]:
    """One ADS-B record → an aircraft Sighting using the aircraft's OWN position
    (if present). None if there's no ICAO address."""
    icao = str(record.get("hex") or record.get("icao") or record.get("addr") or "").strip().lower()
    if not icao:
        return None
    lat = record.get("lat", record.get("latitude"))
    lon = record.get("lon", record.get("longitude"))
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat = lon = None
    flight = str(record.get("flight") or record.get("callsign") or "").strip()
    ts = record.get("now") or record.get("time") or record.get("seen")
    return Sighting(
        kind="adsb", mac=icao, ssid=flight,
        lat=lat, lon=lon,
        ts=_epoch(ts) if not isinstance(ts, (int, float)) else float(ts),
        provenance="dump1090",
    )


def ingest_rtl433(records: Iterable[dict], store, *,
                  position: Optional[Tuple[float, float]] = None) -> int:
    """Convert + batch-insert rtl_433 records; returns how many were stored."""
    batch: List[Sighting] = []
    for r in records:
        s = rtl433_to_sighting(r, position=position)
        if s is not None:
            batch.append(s)
    if batch:
        store.insert_many(batch)
    return len(batch)


def ingest_adsb(records: Iterable[dict], store) -> int:
    """Convert + batch-insert ADS-B records; returns how many were stored."""
    batch: List[Sighting] = []
    for r in records:
        s = adsb_to_sighting(r)
        if s is not None:
            batch.append(s)
    if batch:
        store.insert_many(batch)
    return len(batch)
