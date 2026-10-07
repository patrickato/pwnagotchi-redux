"""WPS-attack / PIN-bruteforce detector.

Passive: counts WPS exchange sightings (M1–M8, NACK, start, identity) aimed
at a target BSSID in a sliding window. A high rate is the classic online PIN
bruteforce / Pixie-adjacent hammer pattern. Pure detection — no TX.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Set, Tuple

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

# Opcodes that count as an attempt step toward a registrar/AP.
_ATTEMPT_OPS = frozenset(
    {
        "m1",
        "m2",
        "m3",
        "m4",
        "m5",
        "m6",
        "m7",
        "m8",
        "nack",
        "start",
        "identity",
        "unknown",
        "",
    }
)


class WPSAttackDetector:
    """Sliding-window rate detector for WPS PIN / registrar abuse."""

    def __init__(
        self,
        *,
        window_s: float = 30.0,
        attempt_threshold: int = 12,
        nack_threshold: int = 6,
    ) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        if attempt_threshold < 1 or nack_threshold < 1:
            raise ValueError("thresholds must be >= 1")
        self.window_s = window_s
        self.attempt_threshold = attempt_threshold
        self.nack_threshold = nack_threshold
        # bssid -> deque of (ts, src, opcode)
        self._events: Dict[str, Deque[Tuple[float, str, str]]] = defaultdict(deque)
        self._fired: Set[str] = set()

    def reset(self) -> None:
        self._events.clear()
        self._fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is not FrameType.WPS:
            return None
        bssid = frame.bssid
        if not bssid:
            return None
        op = frame.wps_opcode if frame.wps_opcode in _ATTEMPT_OPS else "unknown"
        ts = frame.ts
        q = self._events[bssid]
        q.append((ts, frame.src or "", op))
        cutoff = ts - self.window_s
        while q and q[0][0] < cutoff:
            q.popleft()

        attempts = len(q)
        nacks = sum(1 for _, _, o in q if o == "nack")
        srcs = {s for _, s, _ in q if s}

        hit_attempts = attempts >= self.attempt_threshold
        hit_nacks = nacks >= self.nack_threshold
        if not (hit_attempts or hit_nacks):
            return None

        # One alert per BSSID until reset (avoid storms during continuous attack)
        if bssid in self._fired:
            return None
        self._fired.add(bssid)

        parts = []
        if hit_attempts:
            parts.append(
                f"{attempts} WPS exchange steps in {self.window_s:.1f}s "
                f"(threshold {self.attempt_threshold})"
            )
        if hit_nacks:
            parts.append(
                f"{nacks} WPS NACKs in {self.window_s:.1f}s "
                f"(threshold {self.nack_threshold})"
            )
        if srcs:
            parts.append(f"{len(srcs)} distinct source STA(s)")

        reason = f"WPS attack / PIN-bruteforce candidate on BSSID {bssid}: " + "; ".join(
            parts
        )
        return Alert(
            kind=AlertKind.WPS_ATTACK,
            reason=reason,
            ts=ts,
            severity="critical",
            bssid=bssid,
            ssid=frame.ssid,
            detail={
                "attempts": attempts,
                "nacks": nacks,
                "sources": len(srcs),
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
