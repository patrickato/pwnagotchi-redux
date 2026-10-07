"""Surveillance-sweep detector (atlas H-8 remainder).

Flags a rapid arrival of *first-seen* BSSIDs in a short window — the pattern
of an active survey/recon sweep (Pineapple Recon, Marauder scan, wardrive
burst). Distinct from beacon-spam (which counts unique SSIDs/BSSIDs in the
window regardless of prior history): this tracks lifetime first-seen rate.

Pure detection — no TX.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_ADVERT = frozenset({FrameType.BEACON, FrameType.PROBE_RESP})


class SurveillanceSweepDetector:
    """Sliding-window rate of newly observed BSSIDs."""

    def __init__(
        self,
        *,
        window_s: float = 30.0,
        new_bssid_threshold: int = 25,
    ) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        if new_bssid_threshold < 1:
            raise ValueError("new_bssid_threshold must be >= 1")
        self.window_s = window_s
        self.new_bssid_threshold = new_bssid_threshold
        self._seen: Set[str] = set()  # lifetime first-seen set
        self._new_events: Deque[tuple[float, str]] = deque()  # (ts, bssid) first-seens only

    def reset(self) -> None:
        self._seen.clear()
        self._new_events.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type not in _ADVERT:
            return None
        bssid = frame.bssid
        if not bssid:
            return None

        ts = frame.ts
        if bssid in self._seen:
            self._prune(ts)
            return None

        self._seen.add(bssid)
        self._new_events.append((ts, bssid))
        self._prune(ts)

        count = len(self._new_events)
        if count < self.new_bssid_threshold:
            return None

        reason = (
            f"surveillance sweep: {count} first-seen BSSIDs in {self.window_s:.1f}s "
            f"(threshold {self.new_bssid_threshold})"
        )
        self._new_events.clear()

        return Alert(
            kind=AlertKind.SURVEILLANCE_SWEEP,
            reason=reason,
            ts=ts,
            severity="warning",
            detail={
                "new_bssids": count,
                "window_s": self.window_s,
                "threshold": self.new_bssid_threshold,
                "lifetime_seen": len(self._seen),
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
        while self._new_events and self._new_events[0][0] < cutoff:
            self._new_events.popleft()
