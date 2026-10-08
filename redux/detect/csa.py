"""CSA (channel-switch-announcement) abuse detector.

Legitimate CSA is rare and usually once per maintenance window. Rapid or
oscillating CSA from one BSSID is a known forced-roam / denial signature.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Set, Tuple

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class CSAAbuseDetector:
    def __init__(self, *, window_s: float = 30.0, threshold: int = 4) -> None:
        if window_s <= 0 or threshold < 1:
            raise ValueError("window_s > 0 and threshold >= 1 required")
        self.window_s = window_s
        self.threshold = threshold
        self._events: Dict[str, Deque[Tuple[float, Optional[int]]]] = defaultdict(deque)
        self._fired: Set[str] = set()

    def reset(self) -> None:
        self._events.clear()
        self._fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is not FrameType.CSA:
            return None
        bssid = frame.bssid
        if not bssid:
            return None
        new_ch = frame.csa_new_channel if frame.csa_new_channel is not None else frame.channel
        q = self._events[bssid]
        q.append((frame.ts, new_ch))
        cutoff = frame.ts - self.window_s
        while q and q[0][0] < cutoff:
            q.popleft()
        if len(q) < self.threshold or bssid in self._fired:
            return None
        self._fired.add(bssid)
        channels = sorted({c for _, c in q if c is not None})
        reason = (
            f"CSA abuse: BSSID {bssid} announced {len(q)} channel switches "
            f"in {self.window_s:.0f}s (targets {channels or 'unknown'})"
        )
        return Alert(
            kind=AlertKind.CSA_ABUSE,
            reason=reason,
            ts=frame.ts,
            severity="warning",
            bssid=bssid,
            ssid=frame.ssid,
            detail={"count": len(q), "channels": channels},
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
