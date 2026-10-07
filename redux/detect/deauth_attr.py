"""Deauth-source attribution — aggressor STA vs victim AP/clients.

Tracks deauth/disassoc transmitters (src) and targets (dst/bssid) so a
flood alert names the likely aggressor rather than only the victim AP.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Deque, Dict, List, Optional, Set, Tuple

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_DEAUTH_TYPES = frozenset({FrameType.DEAUTH, FrameType.DISASSOC})


class DeauthAttributionDetector:
    """Attribute deauth storms to the dominant transmitter (aggressor)."""

    def __init__(self, *, window_s: float = 5.0, threshold: int = 15) -> None:
        if window_s <= 0 or threshold < 1:
            raise ValueError("window_s > 0 and threshold >= 1 required")
        self.window_s = window_s
        self.threshold = threshold
        # (src, bssid) -> timestamps
        self._events: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._fired: Set[Tuple[str, str]] = set()

    def reset(self) -> None:
        self._events.clear()
        self._fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type not in _DEAUTH_TYPES:
            return None
        src = frame.src or frame.bssid
        bssid = frame.bssid or frame.dst
        if not src or not bssid:
            return None
        key = (src, bssid)
        q = self._events[key]
        q.append(frame.ts)
        cutoff = frame.ts - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) < self.threshold or key in self._fired:
            return None
        self._fired.add(key)
        victim = frame.dst or bssid
        reason = (
            f"deauth attributed: aggressor STA {src} sent {len(q)} "
            f"{frame.type.value} frames toward BSSID {bssid} "
            f"(dst {victim}) in {self.window_s:.1f}s"
        )
        return Alert(
            kind=AlertKind.DEAUTH_FLOOD,
            reason=reason,
            ts=frame.ts,
            severity="critical",
            bssid=bssid,
            detail={
                "aggressor": src,
                "victim_dst": victim,
                "count": len(q),
                "attribution": True,
            },
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
