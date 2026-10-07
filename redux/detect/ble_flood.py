"""BLE advertisement-flood / spam detector (Flipper-style bursts)."""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional, Set, Tuple

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class BLEFloodDetector:
    def __init__(self, *, window_s: float = 5.0, unique_addr_threshold: int = 20) -> None:
        self.window_s = window_s
        self.unique_addr_threshold = unique_addr_threshold
        self._events: Deque[Tuple[float, str]] = deque()
        self._fired = False

    def reset(self) -> None:
        self._events.clear()
        self._fired = False

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is not FrameType.BLE_ADV:
            return None
        addr = frame.ble_addr or frame.src
        if not addr:
            return None
        ts = frame.ts
        self._events.append((ts, addr))
        cutoff = ts - self.window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
        unique = {a for _, a in self._events}
        if len(unique) < self.unique_addr_threshold or self._fired:
            return None
        self._fired = True
        reason = (
            f"BLE advertisement flood: {len(unique)} unique advertisers "
            f"in {self.window_s:.1f}s (threshold {self.unique_addr_threshold})"
        )
        return Alert(
            kind=AlertKind.BLE_FLOOD,
            reason=reason,
            ts=ts,
            severity="warning",
            detail={"unique_addrs": len(unique)},
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
