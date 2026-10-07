"""WiGLE WigleWifi-1.6 CSV export from the sighting store.

Produces the two-row header (device metadata + column names) used by the
WiGLE mobile format, then one data row per sighting. Also exposes a
kismetdb_to_wiglecsv-style dump helper that writes the same CSV body.

No network I/O — pure export. No redux.engine import.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Iterable, List, Optional, TextIO, Union

from redux.geo.db import Sighting, SightingStore

# Practical WigleWifi-1.6 column order (includes Frequency used by modern clients).
WIGLE_VERSION = "1.6"
WIGLE_COLUMNS: List[str] = [
    "MAC",
    "SSID",
    "AuthMode",
    "FirstSeen",
    "Channel",
    "Frequency",
    "RSSI",
    "CurrentLatitude",
    "CurrentLongitude",
    "AltitudeMeters",
    "AccuracyMeters",
    "RCOIs",
    "MfgrId",
    "Type",
]


def channel_to_frequency_mhz(channel: Optional[int]) -> Optional[int]:
    """Map 802.11 channel number to center frequency (MHz), or None."""
    if channel is None or channel <= 0:
        return None
    if 1 <= channel <= 14:
        # 2.4 GHz; ch 14 is 2484
        return 2484 if channel == 14 else 2407 + channel * 5
    if 32 <= channel <= 177:
        # 5 / 6 GHz UNII-style (center = 5000 + 5*ch for many 5 GHz maps)
        return 5000 + channel * 5
    return None


def _format_first_seen(ts: float) -> str:
    """WiGLE FirstSeen: YYYY-MM-DD HH:MM:SS (UTC)."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _auth_mode(sighting: Sighting) -> str:
    if sighting.kind == "ble":
        return "[LE]"
    # Store does not yet carry full RSN IE; leave empty rather than invent.
    return ""


def _type_field(kind: str) -> str:
    k = (kind or "").lower()
    if k == "ble":
        return "BLE"
    if k in ("wifi", "wlan"):
        return "WIFI"
    return "WIFI"  # WiGLE primary table expects WIFI/BLE; SDR stays WIFI-shaped


def _row(sighting: Sighting) -> List[str]:
    freq = channel_to_frequency_mhz(sighting.channel)
    first = sighting.first_seen if sighting.first_seen is not None else sighting.ts
    return [
        (sighting.mac or "").upper(),
        sighting.ssid or "",
        _auth_mode(sighting),
        _format_first_seen(first),
        "" if sighting.channel is None else str(sighting.channel),
        "" if freq is None else str(freq),
        "" if sighting.rssi is None else str(sighting.rssi),
        "" if sighting.lat is None else f"{sighting.lat:.8f}".rstrip("0").rstrip("."),
        "" if sighting.lon is None else f"{sighting.lon:.8f}".rstrip("0").rstrip("."),
        "",  # AltitudeMeters — unknown without GPS altitude
        "",  # AccuracyMeters
        "",  # RCOIs
        "",  # MfgrId
        _type_field(sighting.kind),
    ]


def metadata_header(
    *,
    app_release: str = "redux-geo",
    model: str = "pwnagotchi-redux",
    release: str = "0.0.1",
    device: str = "redux",
    display: str = "",
    board: str = "",
    brand: str = "redux",
) -> str:
    """First header line of a WigleWifi-1.6 file."""
    return (
        f"WigleWifi-{WIGLE_VERSION},"
        f"appRelease={app_release},"
        f"model={model},"
        f"release={release},"
        f"device={device},"
        f"display={display},"
        f"board={board},"
        f"brand={brand}"
    )


def write_wigle_csv(
    sightings: Iterable[Sighting],
    dest: Union[TextIO, str],
    *,
    app_release: str = "redux-geo",
) -> int:
    """Write WigleWifi-1.6 CSV to a file path or text stream. Returns row count."""
    close = False
    if isinstance(dest, str):
        fh: TextIO = open(dest, "w", newline="", encoding="utf-8")
        close = True
    else:
        fh = dest

    try:
        fh.write(metadata_header(app_release=app_release) + "\n")
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(WIGLE_COLUMNS)
        n = 0
        for s in sightings:
            writer.writerow(_row(s))
            n += 1
        return n
    finally:
        if close:
            fh.close()


def export_store(
    store: SightingStore,
    dest: Union[TextIO, str],
    *,
    kind: Optional[str] = None,
    app_release: str = "redux-geo",
) -> int:
    """Export a SightingStore to WiGLE CSV (optional kind filter)."""
    rows = store.query(kind=kind) if kind else store.query()
    # Stable order for tests: by first_seen then mac
    rows = sorted(rows, key=lambda s: (s.first_seen or s.ts, s.mac))
    return write_wigle_csv(rows, dest, app_release=app_release)


def kismetdb_to_wiglecsv_dump(
    sightings: Iterable[Sighting],
) -> str:
    """Return a full WigleWifi-1.6 CSV string (kismetdb_to_wiglecsv-compatible shape)."""
    buf = io.StringIO()
    write_wigle_csv(sightings, buf)
    return buf.getvalue()
