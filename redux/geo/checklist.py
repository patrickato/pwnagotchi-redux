"""Integration checklist for the geo lane (operator-facing)."""
from __future__ import annotations

from typing import List

CHECKLIST: List[str] = [
    "SightingStore opens at the configured path (or :memory: in tests)",
    "Every insert carries non-empty provenance (glass-box)",
    "GPS fixes map into Sighting.lat/lon without inventing coordinates",
    "WiGLE/Kismet imports tag provenance with source filename",
    "Exports (CSV/GPX/KML/GeoJSON) run offline; upload is opt-in external",
    "No redux.engine import from redux.geo (lead wires events)",
    "Hardware-free tests cover insert/query/dedup before field use",
]


def checklist_text() -> str:
    lines = ["Geo integration checklist:"]
    for i, item in enumerate(CHECKLIST, 1):
        lines.append(f"  {i}. {item}")
    return "\n".join(lines)


def checklist_items() -> List[str]:
    return list(CHECKLIST)
