"""Glass-box alerts emitted by detectors."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AlertKind(str, Enum):
    DEAUTH_FLOOD = "deauth_flood"
    ROGUE_AP = "rogue_ap"
    BEACON_SPAM = "beacon_spam"
    SURVEILLANCE_SWEEP = "surveillance_sweep"
    KARMA = "karma"
    CAPTIVE_TWIN = "captive_twin"
    WPS_ATTACK = "wps_attack"
    BLE_TRACKER = "ble_tracker"
    BLE_SKIMMER = "ble_skimmer"
    PMKID_CAPTURE = "pmkid_capture"
    HANDSHAKE_CAPTURE = "handshake_capture"
    BLE_FLOOD = "ble_flood"
    PMF_MISSING = "pmf_missing"
    PINEAPPLE = "pineapple"
    LOUD_PROBER = "loud_prober"
    PINEAPPLE = "pineapple"
    PMF_MISSING = "pmf_missing"
    BLE_FLOOD = "ble_flood"


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    reason: str
    ts: float
    severity: str = "warning"
    bssid: str = ""
    ssid: str = ""
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (self.reason or "").strip():
            raise ValueError("Alert.reason must be a non-empty human-readable string")
