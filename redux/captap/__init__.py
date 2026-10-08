"""redux.captap — the raw 802.11 capture tap (the P0 gap).

bettercap's REST stream doesn't expose raw management frames, which is why
deauth-flood / surveillance-sweep detection and fingerprint PNL/IE re-identification
were dark. This package is the producer: a pure, testable 802.11 parser
(deauth/disassoc + probe-request IE fingerprint) and a tap that routes probe
requests into the fingerprint Dex (real cross-MAC re-id) and deauth/disassoc into
normalized events for the flood detectors. The live monitor socket is the one
needs-hardware adapter; everything else is testable from raw bytes.
"""
from .dot11 import (
    Dot11Frame, parse_dot11, parse_radiotap_len, build_probe_req, build_deauth, build_beacon,
)
from .tap import CaptureTap, DeauthEvent, AccessPoint, live_source, capture_run
from .detect_bridge import to_frame, frames_from

__all__ = [
    "Dot11Frame", "parse_dot11", "parse_radiotap_len", "build_probe_req", "build_deauth",
    "build_beacon",
    "CaptureTap", "DeauthEvent", "AccessPoint", "live_source", "capture_run",
    "to_frame", "frames_from",
]
