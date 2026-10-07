"""Minimal frame / sighting input protocol for the detector pack.

Independent of redux.engine so this lane stays unblocked while the
BettercapDriver lands. A future integration maps bettercap events → Frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FrameType(str, Enum):
    BEACON = "beacon"
    PROBE_RESP = "probe_resp"
    DEAUTH = "deauth"
    DISASSOC = "disassoc"
    WPS = "wps"  # WPS EAP / registrar exchange sighting
    OTHER = "other"


@dataclass(frozen=True)
class Frame:
    """One observed 802.11 management frame (or normalized sighting).

    Fields are tolerant of partial capture: detectors only read what they need.
    """

    type: FrameType
    ts: float  # monotonic or epoch seconds; relative deltas matter
    bssid: str = ""
    ssid: str = ""
    src: str = ""  # transmitter / SA when distinct from BSSID
    dst: str = ""
    channel: Optional[int] = None
    security: str = ""  # e.g. "wpa2-psk", "wpa3-sae", "open", "unknown"
    rssi: Optional[int] = None
    # WPS opcode when type is WPS: m1..m8, nack, done, start, identity, unknown
    wps_opcode: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "bssid", (self.bssid or "").lower())
        object.__setattr__(self, "src", (self.src or "").lower())
        object.__setattr__(self, "dst", (self.dst or "").lower())
        object.__setattr__(self, "security", (self.security or "").lower())
        object.__setattr__(self, "wps_opcode", (self.wps_opcode or "").lower())
