"""Defensive detector pack — pure detection, zero transmit.

Standalone input protocol (Frame) so this module does not depend on
redux.engine (in flight). The lead wires real bettercap Events later.

Every alert carries a human-readable reason (glass-box). Detection only:
never deauths, injects, or targets.
"""
from __future__ import annotations

from redux.detect.alerts import Alert, AlertKind
from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.bus import AlertBus
from redux.detect.config import DEFAULTS, SECTION, _opt, merge_options
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.engine import DetectEngine
from redux.detect.frames import Frame, FrameType
from redux.detect.karma import KarmaCaptiveDetector
from redux.detect.rogue_ap import RogueAPDetector, TrustedNetwork
from redux.detect.surveillance_sweep import SurveillanceSweepDetector
from redux.detect.wps_attack import WPSAttackDetector

__all__ = [
    "Alert",
    "AlertBus",
    "AlertKind",
    "BeaconSpamDetector",
    "DEFAULTS",
    "DeauthFloodDetector",
    "DetectEngine",
    "Frame",
    "FrameType",
    "KarmaCaptiveDetector",
    "RogueAPDetector",
    "SECTION",
    "SurveillanceSweepDetector",
    "TrustedNetwork",
    "WPSAttackDetector",
    "_opt",
    "merge_options",
]
