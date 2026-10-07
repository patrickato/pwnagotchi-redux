"""Defensive detector pack — pure detection, zero transmit.

Standalone input protocol (Frame) so this module does not depend on
redux.engine (in flight). The lead wires real bettercap Events later.

Every alert carries a human-readable reason (glass-box). Detection only:
never deauths, injects, or targets.
"""
from __future__ import annotations

from redux.detect.alerts import Alert, AlertKind
from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.frames import Frame, FrameType
from redux.detect.rogue_ap import RogueAPDetector, TrustedNetwork
from redux.detect.wps_attack import WPSAttackDetector

__all__ = [
    "Alert",
    "AlertKind",
    "BeaconSpamDetector",
    "DeauthFloodDetector",
    "Frame",
    "FrameType",
    "RogueAPDetector",
    "TrustedNetwork",
    "WPSAttackDetector",
]
