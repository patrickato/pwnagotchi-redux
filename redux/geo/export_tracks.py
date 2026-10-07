"""GPX + KML export of sightings from the store."""
from __future__ import annotations

from typing import Iterable, List, Optional
from xml.sax.saxutils import escape

from redux.geo.db import Sighting, SightingStore


def _pts(sightings: Iterable[Sighting]) -> List[Sighting]:
    return [s for s in sightings if s.lat is not None and s.lon is not None]


def to_gpx(sightings: Iterable[Sighting], *, name: str = "redux-geo") -> str:
    pts = _pts(sightings)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="redux-geo">',
        f"  <name>{escape(name)}</name>",
    ]
    for s in pts:
        lines.append(f'  <wpt lat="{s.lat}" lon="{s.lon}">')
        lines.append(f"    <name>{escape(s.mac)}</name>")
        if s.ssid:
            lines.append(f"    <desc>{escape(s.ssid)}</desc>")
        lines.append("  </wpt>")
    lines.append("</gpx>")
    return "\n".join(lines) + "\n"


def to_kml(sightings: Iterable[Sighting], *, name: str = "redux-geo") -> str:
    pts = _pts(sightings)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "  <Document>",
        f"    <name>{escape(name)}</name>",
    ]
    for s in pts:
        lines.append("    <Placemark>")
        lines.append(f"      <name>{escape(s.mac)}</name>")
        if s.ssid:
            lines.append(f"      <description>{escape(s.ssid)}</description>")
        lines.append("      <Point>")
        lines.append(f"        <coordinates>{s.lon},{s.lat},0</coordinates>")
        lines.append("      </Point>")
        lines.append("    </Placemark>")
    lines.append("  </Document>")
    lines.append("</kml>")
    return "\n".join(lines) + "\n"


def export_store_gpx(store: SightingStore, *, kind: Optional[str] = None) -> str:
    rows = store.query(kind=kind) if kind else store.query()
    return to_gpx(rows)


def export_store_kml(store: SightingStore, *, kind: Optional[str] = None) -> str:
    rows = store.query(kind=kind) if kind else store.query()
    return to_kml(rows)
