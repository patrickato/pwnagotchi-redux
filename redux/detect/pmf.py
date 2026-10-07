"""PMF-missing advisory detector.

Flags WPA2 APs that advertise no 802.11w (deauth-vulnerable posture).
Info severity — advisory only, not an attack.
"""
from __future__ import annotations

from typing import List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_ADVERT = frozenset({FrameType.BEACON, FrameType.PROBE_RESP})
_WPA2 = frozenset({"wpa2", "wpa2-psk", "wpa2-eap", "wpa"})


class PMFMissingDetector:
    def __init__(self) -> None:
        self._fired: Set[str] = set()

    def reset(self) -> None:
        self._fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type not in _ADVERT:
            return None
        bssid = frame.bssid
        if not bssid or bssid in self._fired:
            return None
        sec = frame.security
        if not any(sec == s or sec.startswith(s + "-") for s in ("wpa2", "wpa")):
            if sec not in _WPA2:
                return None
        pmf = frame.pmf
        if pmf in ("required", "optional"):
            return None
        # pmf empty or "none" → advisory
        if pmf not in ("", "none", "disabled"):
            return None
        self._fired.add(bssid)
        reason = (
            f"PMF missing: BSSID {bssid} SSID '{frame.ssid or '?'}' "
            f"advertises {sec or 'wpa2'} without 802.11w — deauth-vulnerable posture"
        )
        return Alert(
            kind=AlertKind.PMF_MISSING,
            reason=reason,
            ts=frame.ts,
            severity="info",
            bssid=bssid,
            ssid=frame.ssid,
            detail={"security": sec, "pmf": pmf or "none"},
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
