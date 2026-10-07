"""Detector registry — name, factory, DEFAULTS keys for DetectEngine auto-wiring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from redux.detect.alerts import Alert
from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.config import DEFAULTS, _opt, merge_options
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.frames import Frame
from redux.detect.rogue_ap import RogueAPDetector
from redux.detect.surveillance_sweep import SurveillanceSweepDetector


class _Detector(Protocol):
    def feed(self, frame: Frame) -> Optional[Alert]: ...

    def reset(self) -> None: ...


@dataclass(frozen=True)
class DetectorSpec:
    name: str
    factory: Callable[..., _Detector]
    config_keys: tuple[str, ...]
    description: str


def _deauth_factory(options: Optional[Dict[str, Any]] = None) -> DeauthFloodDetector:
    o = merge_options(options)
    return DeauthFloodDetector(
        window_s=float(_opt(o, "deauth_window_s")),
        threshold=int(_opt(o, "deauth_threshold")),
    )


def _beacon_factory(options: Optional[Dict[str, Any]] = None) -> BeaconSpamDetector:
    o = merge_options(options)
    return BeaconSpamDetector(
        window_s=float(_opt(o, "beacon_spam_window_s")),
        unique_ssid_threshold=int(_opt(o, "beacon_spam_unique_ssid_threshold")),
        unique_bssid_threshold=int(_opt(o, "beacon_spam_unique_bssid_threshold")),
    )


def _sweep_factory(options: Optional[Dict[str, Any]] = None) -> SurveillanceSweepDetector:
    o = merge_options(options)
    return SurveillanceSweepDetector(
        window_s=float(_opt(o, "sweep_window_s")),
        new_bssid_threshold=int(_opt(o, "sweep_new_bssid_threshold")),
    )


def _rogue_factory(options: Optional[Dict[str, Any]] = None) -> RogueAPDetector:
    return RogueAPDetector(())  # trusted list supplied by caller/engine


REGISTRY: List[DetectorSpec] = [
    DetectorSpec(
        "deauth_flood",
        _deauth_factory,
        ("deauth_window_s", "deauth_threshold"),
        "Deauth/disassoc flood rate detector",
    ),
    DetectorSpec(
        "beacon_spam",
        _beacon_factory,
        (
            "beacon_spam_window_s",
            "beacon_spam_unique_ssid_threshold",
            "beacon_spam_unique_bssid_threshold",
        ),
        "Beacon unique-SSID/BSSID flood detector",
    ),
    DetectorSpec(
        "surveillance_sweep",
        _sweep_factory,
        ("sweep_window_s", "sweep_new_bssid_threshold"),
        "First-seen BSSID rate (survey sweep)",
    ),
    DetectorSpec(
        "rogue_ap",
        _rogue_factory,
        (),
        "Trusted-SSID mismatch (evil twin) detector",
    ),
]


def list_detectors() -> List[str]:
    return [s.name for s in REGISTRY]


def build_from_registry(
    names: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> List[_Detector]:
    """Instantiate detectors; None names = all registered."""
    want = set(names) if names is not None else {s.name for s in REGISTRY}
    out: List[_Detector] = []
    for spec in REGISTRY:
        if spec.name in want:
            out.append(spec.factory(options))
    return out
