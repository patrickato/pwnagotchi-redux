"""Sighting validation / hardening helpers (glass-box rejections)."""
from __future__ import annotations

from typing import List, Tuple

from redux.geo.db import Sighting


def validate_sighting(s: Sighting) -> Tuple[bool, str]:
    """Return (ok, reason). Rejects empty mac, out-of-range coords/rssi."""
    if not (s.mac or "").strip():
        return False, "reject: empty mac"
    if s.lat is not None and not (-90.0 <= float(s.lat) <= 90.0):
        return False, f"reject: lat {s.lat} out of range"
    if s.lon is not None and not (-180.0 <= float(s.lon) <= 180.0):
        return False, f"reject: lon {s.lon} out of range"
    if s.rssi is not None and not (-120 <= int(s.rssi) <= 0):
        return False, f"reject: rssi {s.rssi} out of plausible range"
    if s.channel is not None and int(s.channel) < 0:
        return False, f"reject: channel {s.channel} negative"
    if not (s.provenance or "").strip():
        return False, "reject: empty provenance (glass-box required)"
    return True, "ok: sighting passes validation"


def filter_valid(sightings: List[Sighting]) -> Tuple[List[Sighting], List[str]]:
    """Split into valid rows and rejection reasons."""
    good: List[Sighting] = []
    reasons: List[str] = []
    for s in sightings:
        ok, reason = validate_sighting(s)
        if ok:
            good.append(s)
        else:
            reasons.append(f"{s.mac or '?'}: {reason}")
    return good, reasons
