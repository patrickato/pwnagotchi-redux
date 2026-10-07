"""Hidden-SSID reveal detector.

Remembers BSSIDs that advertised empty-SSID beacons (cloaked). When a later
probe response (or non-empty beacon) discloses an SSID for that BSSID, emit
a glass-box info alert — passive recon of cloaked network names.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class HiddenSSIDRevealDetector:
    def __init__(self) -> None:
        self._cloaked: Dict[str, float] = {}
        self._revealed: Set[str] = set()

    def reset(self) -> None:
        self._cloaked.clear()
        self._revealed.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        bssid = frame.bssid
        if not bssid:
            return None

        if frame.type is FrameType.BEACON:
            ssid = (frame.ssid or "").strip()
            if not ssid:
                if bssid not in self._cloaked:
                    self._cloaked[bssid] = frame.ts
                return None
            return self._maybe_reveal(bssid, ssid, frame.ts, "beacon")

        if frame.type is FrameType.PROBE_RESP:
            ssid = (frame.ssid or "").strip()
            if not ssid:
                return None
            return self._maybe_reveal(bssid, ssid, frame.ts, "probe_resp")

        return None

    def _maybe_reveal(
        self, bssid: str, ssid: str, ts: float, via: str
    ) -> Optional[Alert]:
        if bssid not in self._cloaked or bssid in self._revealed:
            return None
        self._revealed.add(bssid)
        cloaked_at = self._cloaked[bssid]
        reason = (
            f"hidden SSID revealed: BSSID {bssid} was cloaked (empty-SSID beacon "
            f"at ts={cloaked_at:.1f}); SSID '{ssid}' disclosed via {via}"
        )
        return Alert(
            kind=AlertKind.HIDDEN_SSID_REVEAL,
            reason=reason,
            ts=ts,
            severity="info",
            bssid=bssid,
            ssid=ssid,
            detail={
                "reveal": True,
                "via": via,
                "cloaked_ts": cloaked_at,
            },
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
