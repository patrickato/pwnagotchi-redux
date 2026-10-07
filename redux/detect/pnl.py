"""Probe-request PNL harvester + loud-prober detector.

Builds a device→preferred-network-list view from probe requests and alerts
when one STA probes for many distinct SSIDs (loud prober).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class PNLHarvester:
    """Accumulate probe SSIDs per STA (src)."""

    def __init__(self, *, loud_threshold: int = 8) -> None:
        if loud_threshold < 1:
            raise ValueError("loud_threshold must be >= 1")
        self.loud_threshold = loud_threshold
        self._pnl: Dict[str, Set[str]] = defaultdict(set)
        self._fired: Set[str] = set()

    def reset(self) -> None:
        self._pnl.clear()
        self._fired.clear()

    def pnl_for(self, sta: str) -> Set[str]:
        return set(self._pnl.get((sta or "").lower(), ()))

    def all_pnls(self) -> Dict[str, Set[str]]:
        return {k: set(v) for k, v in self._pnl.items()}

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is not FrameType.PROBE_REQ:
            return None
        sta = frame.src
        ssid = (frame.ssid or "").strip()
        if not sta or not ssid:
            return None
        self._pnl[sta].add(ssid)
        n = len(self._pnl[sta])
        if n < self.loud_threshold or sta in self._fired:
            return None
        self._fired.add(sta)
        reason = (
            f"loud prober: STA {sta} probed for {n} distinct SSIDs "
            f"(threshold {self.loud_threshold}) — preferred-network list harvest"
        )
        return Alert(
            kind=AlertKind.LOUD_PROBER,
            reason=reason,
            ts=frame.ts,
            severity="warning",
            bssid=sta,
            detail={"ssid_count": n, "ssids": sorted(self._pnl[sta])[:20]},
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
