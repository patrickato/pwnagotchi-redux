"""Unique-client burst detector — many distinct STA addresses in a short window.

Useful for spotting mass-probe or surveillance sweeps of clients.
Passive only.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_CLIENT_TYPES = frozenset({
    FrameType.PROBE_REQ,
    FrameType.PROBE_RESP,
    FrameType.BEACON,
    FrameType.DEAUTH,
    FrameType.DISASSOC,
})


class ClientBurstDetector:
    """Flag when many unique src MACs appear inside window_s."""

    def __init__(self, *, window_s: float = 15.0, unique_threshold: int = 30) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        if unique_threshold < 1:
            raise ValueError("unique_threshold must be >= 1")
        self.window_s = window_s
        self.unique_threshold = unique_threshold
        self._events: Deque[tuple[float, str]] = deque()

    def reset(self) -> None:
        self._events.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type not in _CLIENT_TYPES:
            return None
        src = (frame.src or "").strip()
        if not src:
            return None
        ts = float(frame.ts)
        self._events.append((ts, src))
        cutoff = ts - self.window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
        unique: Set[str] = {s for _, s in self._events}
        n = len(unique)
        if n < self.unique_threshold:
            return None
        reason = (
            f"client burst: {n} unique STA addresses in {self.window_s:.1f}s "
            f"(threshold={self.unique_threshold})"
        )
        return Alert(
            kind=AlertKind.CLIENT_BURST,
            reason=reason,
            ts=ts,
            severity="warning",
            detail={"unique": n, "window_s": self.window_s, "threshold": self.unique_threshold},
        )
