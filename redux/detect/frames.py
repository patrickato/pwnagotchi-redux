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
    BLE_ADV = "ble_adv"  # BLE advertisement / scan response
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
    # BLE advertisement fields (type BLE_ADV)
    ble_addr: str = ""  # advertiser address
    ble_name: str = ""  # complete/short local name
    ble_company_id: str = ""  # e.g. "0x004c" Apple
    ble_service_uuid: str = ""  # 16/128-bit UUID string if known

    def __post_init__(self) -> None:
        object.__setattr__(self, "bssid", (self.bssid or "").lower())
        object.__setattr__(self, "src", (self.src or "").lower())
        object.__setattr__(self, "dst", (self.dst or "").lower())
        object.__setattr__(self, "security", (self.security or "").lower())
        object.__setattr__(self, "wps_opcode", (self.wps_opcode or "").lower())
        object.__setattr__(self, "ble_addr", (self.ble_addr or "").lower())
        object.__setattr__(self, "ble_name", self.ble_name or "")
        object.__setattr__(self, "ble_company_id", (self.ble_company_id or "").lower())
        object.__setattr__(self, "ble_service_uuid", (self.ble_service_uuid or "").lower())
