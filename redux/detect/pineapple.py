"""Pineapple / PineAP / MANA detector.

Signatures: one BSSID probe-responds to many distinct SSIDs (karma-at-scale),
and/or one BSSID beacon-floods many SSIDs in a window.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Set, Tuple

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class PineappleDetector:
    def __init__(
        self,
        *,
        window_s: float = 20.0,
        probe_ssid_threshold: int = 10,
        beacon_ssid_threshold: int = 15,
    ) -> None:
        self.window_s = window_s
        self.probe_ssid_threshold = probe_ssid_threshold
        self.beacon_ssid_threshold = beacon_ssid_threshold
        self._probe: Dict[str, Deque[Tuple[float, str]]] = defaultdict(deque)
        self._beacon: Dict[str, Deque[Tuple[float, str]]] = defaultdict(deque)
        self._fired: Set[str] = set()

    def reset(self) -> None:
        self._probe.clear()
        self._beacon.clear()
        self._fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        bssid = frame.bssid
        if not bssid or not frame.ssid:
            return None
        if frame.type is FrameType.PROBE_RESP:
            return self._track(self._probe, bssid, frame, self.probe_ssid_threshold, "probe-response")
        if frame.type is FrameType.BEACON:
            return self._track(self._beacon, bssid, frame, self.beacon_ssid_threshold, "beacon-flood")
        return None

    def _track(
        self,
        store: Dict[str, Deque[Tuple[float, str]]],
        bssid: str,
        frame: Frame,
        threshold: int,
        mode: str,
    ) -> Optional[Alert]:
        q = store[bssid]
        q.append((frame.ts, frame.ssid))
        cutoff = frame.ts - self.window_s
        while q and q[0][0] < cutoff:
            q.popleft()
        unique = {s for _, s in q}
        if len(unique) < threshold or bssid in self._fired:
            return None
        self._fired.add(bssid)
        reason = (
            f"Pineapple/MANA candidate: BSSID {bssid} {mode} for "
            f"{len(unique)} distinct SSIDs in {self.window_s:.0f}s "
            f"(threshold {threshold})"
        )
        return Alert(
            kind=AlertKind.PINEAPPLE,
            reason=reason,
            ts=frame.ts,
            severity="critical",
            bssid=bssid,
            detail={"unique_ssids": len(unique), "mode": mode},
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
