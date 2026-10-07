"""PMKID / EAPOL handshake capture observer.

Passive: watches for PMKID sightings and complete EAPOL M1–M4 sets per
BSSID, then emits glass-box alerts the creature/supervisor can narrate.
Does not crack, transmit, or target — observation only.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class HandshakeCaptureDetector:
    """Track PMKID frames and EAPOL message sets per BSSID."""

    def __init__(self, *, handshake_window_s: float = 30.0) -> None:
        if handshake_window_s <= 0:
            raise ValueError("handshake_window_s must be positive")
        self.handshake_window_s = handshake_window_s
        # bssid -> set of eapol msg nums with last ts
        self._eapol: Dict[str, Dict[int, float]] = defaultdict(dict)
        self._pmkid_fired: Set[str] = set()
        self._hs_fired: Set[str] = set()

    def reset(self) -> None:
        self._eapol.clear()
        self._pmkid_fired.clear()
        self._hs_fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is FrameType.PMKID:
            return self._on_pmkid(frame)
        if frame.type is FrameType.EAPOL:
            return self._on_eapol(frame)
        return None

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out

    def _on_pmkid(self, frame: Frame) -> Optional[Alert]:
        bssid = frame.bssid
        if not bssid:
            return None
        if bssid in self._pmkid_fired:
            return None
        self._pmkid_fired.add(bssid)
        ssid = frame.ssid or "(unknown SSID)"
        reason = (
            f"PMKID observed on BSSID {bssid} SSID '{ssid}' "
            f"(clientless RSN IE)"
        )
        return Alert(
            kind=AlertKind.PMKID_CAPTURE,
            reason=reason,
            ts=frame.ts,
            severity="info",
            bssid=bssid,
            ssid=frame.ssid,
            detail={"channel": frame.channel, "rssi": frame.rssi},
        )

    def _on_eapol(self, frame: Frame) -> Optional[Alert]:
        bssid = frame.bssid
        if not bssid:
            return None
        msg = frame.eapol_msg
        if msg < 1 or msg > 4:
            return None

        bucket = self._eapol[bssid]
        # Drop stale messages outside window relative to this frame
        cutoff = frame.ts - self.handshake_window_s
        stale = [m for m, t in bucket.items() if t < cutoff]
        for m in stale:
            del bucket[m]

        bucket[msg] = frame.ts
        if not ({1, 2, 3, 4} <= set(bucket.keys())):
            return None
        if bssid in self._hs_fired:
            return None
        self._hs_fired.add(bssid)

        ssid = frame.ssid or "(unknown SSID)"
        reason = (
            f"full EAPOL handshake (M1–M4) observed on BSSID {bssid} "
            f"SSID '{ssid}' within {self.handshake_window_s:.0f}s"
        )
        return Alert(
            kind=AlertKind.HANDSHAKE_CAPTURE,
            reason=reason,
            ts=frame.ts,
            severity="info",
            bssid=bssid,
            ssid=frame.ssid,
            detail={
                "messages": sorted(bucket.keys()),
                "window_s": self.handshake_window_s,
            },
        )
