"""Detector registry — every detector name, factory, config keys.

DetectEngine builds its detector list from REGISTRY so new modules
auto-include once registered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from redux.detect.alerts import Alert
from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.ble_flood import BLEFloodDetector
from redux.detect.ble_tracker import BLETrackerDetector
from redux.detect.config import DEFAULTS, _opt, merge_options
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.frames import Frame
from redux.detect.handshake import HandshakeCaptureDetector
from redux.detect.hidden_ssid import HiddenSSIDRevealDetector
from redux.detect.karma import KarmaCaptiveDetector
from redux.detect.pineapple import PineappleDetector
from redux.detect.pmf import PMFMissingDetector
from redux.detect.pnl import PNLHarvester
from redux.detect.rogue_ap import RogueAPDetector
from redux.detect.surveillance_sweep import SurveillanceSweepDetector
from redux.detect.wps_attack import WPSAttackDetector


class _Detector(Protocol):
    def feed(self, frame: Frame) -> Optional[Alert]: ...

    def reset(self) -> None: ...


@dataclass(frozen=True)
class DetectorSpec:
    name: str
    factory: Callable[..., _Detector]
    config_keys: tuple[str, ...]
    description: str


def _deauth(o: Optional[Dict[str, Any]] = None) -> DeauthFloodDetector:
    x = merge_options(o)
    return DeauthFloodDetector(
        window_s=float(_opt(x, "deauth_window_s")),
        threshold=int(_opt(x, "deauth_threshold")),
    )


def _beacon(o: Optional[Dict[str, Any]] = None) -> BeaconSpamDetector:
    x = merge_options(o)
    return BeaconSpamDetector(
        window_s=float(_opt(x, "beacon_spam_window_s")),
        unique_ssid_threshold=int(_opt(x, "beacon_spam_unique_ssid_threshold")),
        unique_bssid_threshold=int(_opt(x, "beacon_spam_unique_bssid_threshold")),
    )


def _sweep(o: Optional[Dict[str, Any]] = None) -> SurveillanceSweepDetector:
    x = merge_options(o)
    return SurveillanceSweepDetector(
        window_s=float(_opt(x, "sweep_window_s")),
        new_bssid_threshold=int(_opt(x, "sweep_new_bssid_threshold")),
    )


def _rogue(o: Optional[Dict[str, Any]] = None) -> RogueAPDetector:
    return RogueAPDetector(())


def _karma(o: Optional[Dict[str, Any]] = None) -> KarmaCaptiveDetector:
    x = merge_options(o)
    return KarmaCaptiveDetector(
        karma_window_s=float(_opt(x, "karma_window_s")),
        karma_ssid_threshold=int(_opt(x, "karma_ssid_threshold")),
    )


def _wps(o: Optional[Dict[str, Any]] = None) -> WPSAttackDetector:
    x = merge_options(o)
    return WPSAttackDetector(
        window_s=float(_opt(x, "wps_window_s")),
        attempt_threshold=int(_opt(x, "wps_attempt_threshold")),
        nack_threshold=int(_opt(x, "wps_nack_threshold")),
    )


def _ble_tracker(o: Optional[Dict[str, Any]] = None) -> BLETrackerDetector:
    return BLETrackerDetector()


def _hidden_ssid(o: Optional[Dict[str, Any]] = None) -> HiddenSSIDRevealDetector:
    return HiddenSSIDRevealDetector()


def _handshake(o: Optional[Dict[str, Any]] = None) -> HandshakeCaptureDetector:
    x = merge_options(o)
    return HandshakeCaptureDetector(
        handshake_window_s=float(_opt(x, "handshake_window_s")),
    )


def _ble_flood(o: Optional[Dict[str, Any]] = None) -> BLEFloodDetector:
    x = merge_options(o)
    return BLEFloodDetector(
        window_s=float(_opt(x, "ble_flood_window_s")),
        unique_addr_threshold=int(_opt(x, "ble_flood_unique_addr_threshold")),
    )


def _pmf(o: Optional[Dict[str, Any]] = None) -> PMFMissingDetector:
    return PMFMissingDetector()


def _pineapple(o: Optional[Dict[str, Any]] = None) -> PineappleDetector:
    x = merge_options(o)
    return PineappleDetector(
        window_s=float(_opt(x, "pineapple_window_s")),
        probe_ssid_threshold=int(_opt(x, "pineapple_probe_ssid_threshold")),
        beacon_ssid_threshold=int(_opt(x, "pineapple_beacon_ssid_threshold")),
    )


def _pnl(o: Optional[Dict[str, Any]] = None) -> PNLHarvester:
    x = merge_options(o)
    return PNLHarvester(loud_threshold=int(_opt(x, "pnl_loud_threshold")))


REGISTRY: List[DetectorSpec] = [
    DetectorSpec("deauth_flood", _deauth, ("deauth_window_s", "deauth_threshold"), "Deauth/disassoc flood"),
    DetectorSpec(
        "beacon_spam",
        _beacon,
        ("beacon_spam_window_s", "beacon_spam_unique_ssid_threshold", "beacon_spam_unique_bssid_threshold"),
        "Beacon unique-SSID/BSSID flood",
    ),
    DetectorSpec("surveillance_sweep", _sweep, ("sweep_window_s", "sweep_new_bssid_threshold"), "First-seen BSSID rate"),
    DetectorSpec("rogue_ap", _rogue, (), "Trusted-SSID mismatch (evil twin)"),
    DetectorSpec("karma", _karma, ("karma_window_s", "karma_ssid_threshold"), "Karma multi-SSID probe response"),
    DetectorSpec("wps_attack", _wps, ("wps_window_s", "wps_attempt_threshold", "wps_nack_threshold"), "WPS attempt/NACK rate"),
    DetectorSpec("ble_tracker", _ble_tracker, (), "Apple Find My / HC-0x skimmer BLE"),
    DetectorSpec("handshake", _handshake, ("handshake_window_s",), "PMKID / EAPOL M1–M4 capture"),
    DetectorSpec("hidden_ssid", _hidden_ssid, (), "Hidden-SSID reveal (cloaked name disclosed)"),
    DetectorSpec("ble_flood", _ble_flood, ("ble_flood_window_s", "ble_flood_unique_addr_threshold"), "BLE advertisement spam"),
    DetectorSpec("pmf_missing", _pmf, (), "WPA2 without 802.11w advisory"),
    DetectorSpec(
        "pineapple",
        _pineapple,
        ("pineapple_window_s", "pineapple_probe_ssid_threshold", "pineapple_beacon_ssid_threshold"),
        "Pineapple/MANA multi-SSID responder",
    ),
    DetectorSpec("pnl", _pnl, ("pnl_loud_threshold",), "Probe-request PNL / loud prober"),
]


def list_detectors() -> List[str]:
    return [s.name for s in REGISTRY]


def build_from_registry(
    names: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> List[_Detector]:
    want = set(names) if names is not None else {s.name for s in REGISTRY}
    out: List[_Detector] = []
    for spec in REGISTRY:
        if spec.name in want:
            out.append(spec.factory(options))
    return out
