"""More SDR decoders → geo-tagged Sightings: POCSAG pagers and ACARS aircraft.

Same shape as the rtl_433 / ADS-B ingest: parse a decoder's output into
Sightings written to the SAME store, so pagers and ACARS aircraft show up in the
Field Dex alongside WiFi/BLE/ISM. Readers are injected (recorded samples in
tests, a live subprocess on hardware), so parsing is fully testable with no
radio.

**Privacy by design.** A POCSAG pager message, or an ACARS free-text field, can
carry personal or operational content (hospital pagers, crew messages). We treat
these exactly like an ISM sensor id or an ADS-B hex: the Sighting records the
*identity* heard here — the pager capcode, the aircraft tail — **never the
message body**. `parse_pocsag_line` still returns the full decode so the
operator can inspect it live, but `*_to_sighting` deliberately drops the text
before anything is persisted. Real data only: a record with no usable id is
skipped.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from redux.geo import Sighting
from .ingest import _epoch, _SANITIZE

# multimon-ng POCSAG line, e.g.:
#   POCSAG1200: Address: 1234567  Function: 3  Alpha:   hello world
#   POCSAG512:  Address:  999999  Function: 0  Numeric: 12345
_POCSAG = re.compile(
    r"POCSAG(?P<baud>\d+):\s*Address:\s*(?P<addr>\d+)\s*"
    r"Function:\s*(?P<func>\d+)\s*"
    r"(?:(?P<kind>Alpha|Numeric|Skyper):?\s*(?P<msg>.*))?$"
)


def parse_pocsag_line(line: str) -> Optional[dict]:
    """Parse one multimon-ng POCSAG line into a dict (incl. the message, for live
    operator inspection). None if the line isn't a POCSAG decode."""
    m = _POCSAG.search(line or "")
    if not m:
        return None
    return {
        "protocol": f"POCSAG{m.group('baud')}",
        "address": m.group("addr"),
        "function": int(m.group("func")),
        "msg_kind": (m.group("kind") or "").lower(),
        "message": (m.group("msg") or "").strip(),   # NOT persisted to a Sighting
    }


def pocsag_to_sighting(parsed: dict, *,
                       position: Optional[Tuple[float, float]] = None,
                       ts: float = 0.0) -> Optional[Sighting]:
    """A parsed POCSAG decode → a pager Sighting keyed by CAPCODE only. The
    message body is intentionally dropped (privacy). Tagged with the receiver's
    position, like any ISM sighting."""
    addr = str(parsed.get("address") or "").strip()
    if not addr:
        return None
    proto = str(parsed.get("protocol") or "POCSAG")
    key = _SANITIZE.sub("-", f"pocsag_{addr}").strip("-").lower()
    lat = lon = None
    if position:
        lat, lon = position[0], position[1]
    return Sighting(kind="pocsag", mac=key, ssid=proto,
                    lat=lat, lon=lon, ts=ts, provenance="multimon-ng")


def ingest_pocsag(lines: Iterable[str], store, *,
                  position: Optional[Tuple[float, float]] = None,
                  now: float = 0.0) -> int:
    """Parse multimon-ng POCSAG lines and batch-insert capcode sightings.
    Returns how many were stored (message bodies are never persisted)."""
    batch: List[Sighting] = []
    for line in lines:
        parsed = parse_pocsag_line(line)
        if parsed is None:
            continue
        s = pocsag_to_sighting(parsed, position=position, ts=now)
        if s is not None:
            batch.append(s)
    if batch:
        store.insert_many(batch)
    return len(batch)


def acars_to_sighting(record: dict, *,
                      position: Optional[Tuple[float, float]] = None) -> Optional[Sighting]:
    """One acarsdec JSON record → an aircraft Sighting keyed by tail/registration
    (or flight). The free-text message is dropped (privacy). ACARS carries no
    position, so it's tagged with the receiver's position like ISM."""
    tail = str(record.get("tail") or record.get("registration") or "").strip()
    flight = str(record.get("flight") or record.get("callsign") or "").strip()
    ident = tail or flight
    if not ident:
        return None
    key = _SANITIZE.sub("-", f"acars_{ident}").strip("-").lower()
    lat = lon = None
    if position:
        lat, lon = position[0], position[1]
    ts = record.get("timestamp") or record.get("time") or 0.0
    ts = float(ts) if isinstance(ts, (int, float)) else _epoch(ts)
    return Sighting(kind="acars", mac=key, ssid=flight or tail,
                    lat=lat, lon=lon, ts=ts, provenance="acarsdec")


def ingest_acars(records: Iterable[dict], store, *,
                 position: Optional[Tuple[float, float]] = None) -> int:
    """Convert + batch-insert acarsdec records; returns how many were stored."""
    batch: List[Sighting] = []
    for r in records:
        s = acars_to_sighting(r, position=position)
        if s is not None:
            batch.append(s)
    if batch:
        store.insert_many(batch)
    return len(batch)
