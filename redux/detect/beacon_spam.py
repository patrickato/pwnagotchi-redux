"""Beacon-spam detector (atlas H-8).

Flags an abnormally high rate of *distinct* SSIDs (or BSSIDs) advertised
via beacons in a sliding window — the classic "beacon flood" / fake-AP
spray pattern. Pure detection — no TX.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType


class BeaconSpamDetector:
    """Sliding-window unique-SSID (or BSSID) rate detector for beacon floods."""

    def __init__(
        self,
        *,
        window_s: float = 10.0,
        unique_ssid_threshold: int = 30,
        unique_bssid_threshold: int = 40,
    ) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        if unique_ssid_threshold < 1 or unique_bssid_threshold < 1:
            raise ValueError("thresholds must be >= 1")
        self.window_s = window_s
        self.unique_ssid_threshold = unique_ssid_threshold
        self.unique_bssid_threshold = unique_bssid_threshold
        self._events: Deque[tuple[float, str, str]] = deque()  # (ts, ssid, bssid)

    def reset(self) -> None:
        self._events.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is not FrameType.BEACON:
            return None

        ts = frame.ts
        self._events.append((ts, frame.ssid or "", frame.bssid or ""))
        self._prune(ts)

        ssids: Set[str] = {s for _, s, _ in self._events if s}
        bssids: Set[str] = {b for _, _, b in self._events if b}

        hit_ssid = len(ssids) >= self.unique_ssid_threshold
        hit_bssid = len(bssids) >= self.unique_bssid_threshold
        if not (hit_ssid or hit_bssid):
            return None

        parts = []
        if hit_ssid:
            parts.append(
                f"{len(ssids)} unique SSIDs in {self.window_s:.1f}s "
                f"(threshold {self.unique_ssid_threshold})"
            )
        if hit_bssid:
            parts.append(
                f"{len(bssids)} unique BSSIDs in {self.window_s:.1f}s "
                f"(threshold {self.unique_bssid_threshold})"
            )
        reason = "beacon spam: " + "; ".join(parts)

        self._events.clear()

        return Alert(
            kind=AlertKind.BEACON_SPAM,
            reason=reason,
            ts=ts,
            severity="warning",
            detail={
                "unique_ssids": len(ssids),
                "unique_bssids": len(bssids),
                "window_s": self.window_s,
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
