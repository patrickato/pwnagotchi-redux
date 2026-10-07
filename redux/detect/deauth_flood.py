"""Deauth / disassoc flood detector.

Counts deauth+disassoc frames in a sliding time window. When the rate
exceeds a threshold, emits a glass-box alert. Pure detection — no TX.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_MGMT_KILL = frozenset({FrameType.DEAUTH, FrameType.DISASSOC})


class DeauthFloodDetector:
    """Sliding-window rate detector for deauth/disassoc bursts.

    Defaults are conservative for lab/demo use; tune via constructor.
    """

    def __init(
        self,
        *,
        window_s: float = 5.0,
        threshold: int = 20,
    ) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.window_s = window_s
        self.threshold = threshold
        self._events: Deque[tuple[float, str]] = deque()  # (ts, bssid)

    def reset(self) -> None:
        self._events.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        """Ingest one frame. Returns an Alert on threshold breach, else None."""
        if frame.type not in _MGMT_KILL:
            return None

        ts = frame.ts
        self._events.append((ts, frame.bssid))
        self._prune(ts)

        count = len(self._events)
        if count < self.threshold:
            return None

        # Attribute to the most frequent BSSID in the window when present.
        by_bssid: dict[str, int] = {}
        for _, b in self._events:
            if b:
                by_bssid[b] = by_bssid.get(b, 0) + 1
        top_bssid = ""
        top_n = 0
        for b, n in by_bssid.items():
            if n > top_n:
                top_bssid, top_n = b, n

        reason = (
            f"deauth/disassoc flood: {count} frames in {self.window_s:.1f}s "
            f"(threshold {self.threshold})"
        )
        if top_bssid:
            reason += f"; dominant BSSID {top_bssid} ({top_n} frames)"

        # Avoid alert storms: clear window after firing so the next burst
        # must rebuild. Caller can still see continuous floods as discrete alerts.
        self._events.clear()

        return Alert(
            kind=AlertKind.DEAUTH_FLOOD,
            reason=reason,
            ts=ts,
            severity="critical",
            bssid=top_bssid,
            detail={
                "count": count,
                "window_s": self.window_s,
                "threshold": self.threshold,
            },
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
