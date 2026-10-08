"""GeoJSON FeatureCollection export of sightings / estimates."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from redux.geo.db import Sighting, SightingStore


def sighting_to_feature(s: Sighting) -> Optional[Dict[str, Any]]:
    if s.lat is None or s.lon is None:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [s.lon, s.lat]},
        "properties": {
            "mac": s.mac,
            "ssid": s.ssid,
            "kind": s.kind,
            "rssi": s.rssi,
            "channel": s.channel,
            "ts": s.ts,
            "provenance": s.provenance,
        },
    }


def to_feature_collection(sightings: Iterable[Sighting]) -> Dict[str, Any]:
    features: List[Dict[str, Any]] = []
    for s in sightings:
        f = sighting_to_feature(s)
        if f is not None:
            features.append(f)
    return {"type": "FeatureCollection", "features": features}


def export_store_geojson(store: SightingStore, *, kind: Optional[str] = None) -> Dict[str, Any]:
    rows = store.query(kind=kind) if kind else store.query()
    return to_feature_collection(rows)
