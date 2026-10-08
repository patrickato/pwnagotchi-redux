"""Probe-request flood detector — high rate of probe_req frames.

Passive only; raises glass-box alerts, transmits nothing.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class ProbeFloodDetector:
    """Sliding-window rate detector for probe-request bursts."""

    def __init__(self, *, window_s: float = 5.0, threshold: int = 40) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.window_s = window_s
        self.threshold = threshold
        self._events: Deque[float] = deque()

    def reset(self) -> None:
        self._events.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is not FrameType.PROBE_REQ:
            return None
        ts = float(frame.ts)
        self._events.append(ts)
        cutoff = ts - self.window_s
        while self._events and self._events[0] < cutoff:
            self._events.popleft()
        n = len(self._events)
        if n < self.threshold:
            return None
        reason = (
            f"probe flood: {n} probe_req frames in {self.window_s:.1f}s "
            f"(threshold={self.threshold})"
        )
        return Alert(
            kind=AlertKind.PROBE_FLOOD,
            reason=reason,
            ts=ts,
            severity="warning",
            bssid=frame.bssid or "",
            ssid=frame.ssid or "",
            detail={"count": n, "window_s": self.window_s, "threshold": self.threshold},
        )
