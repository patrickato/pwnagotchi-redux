"""Minimal frame / sighting input protocol for the detector pack."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FrameType(str, Enum):
    BEACON = "beacon"
    PROBE_REQ = "probe_req"
    PROBE_RESP = "probe_resp"
    DEAUTH = "deauth"
    DISASSOC = "disassoc"
    WPS = "wps"
    BLE_ADV = "ble_adv"
    PMKID = "pmkid"
    EAPOL = "eapol"
    OTHER = "other"


@dataclass(frozen=True)
class Frame:
    type: FrameType
    ts: float
    bssid: str = ""
    ssid: str = ""
    src: str = ""
    dst: str = ""
    channel: Optional[int] = None
    security: str = ""
    rssi: Optional[int] = None
    wps_opcode: str = ""
    ble_addr: str = ""
    ble_name: str = ""
    ble_company_id: str = ""
    ble_service_uuid: str = ""
    eapol_msg: int = 0
    pmf: str = ""  # required | optional | none |

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
        object.__setattr__(self, "eapol_msg", int(self.eapol_msg or 0))
        object.__setattr__(self, "pmf", (self.pmf or "").lower())
