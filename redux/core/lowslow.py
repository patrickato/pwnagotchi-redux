"""Low-and-slow capture scheduler — the temporal half of stealth.

Quiet mode (#80) caps *how much* you emit and the footprint meter (#81) scores
*how loud* you are. This adds the missing dimension: *when*. It paces offensive
emissions over time so the running footprint stays under a target — and jitters
the spacing so the cadence itself isn't a signature (a perfectly regular deauth
every 10s is as detectable as a loud burst).

A + B ⇒ C again: A (Quiet's frame budget + per-action confirm) + B (the footprint
event weights) ⇒ C (a scheduler that releases the next action only when a sliding
window has room and a jittered minimum gap has elapsed). It never loosens a gate —
it sits on top of Quiet and the scope/power rules and only ever says "not yet."

Pure/testable: injected clock, seeded jitter — same inputs, same schedule.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .stealth import _EVENT_WEIGHT, QuietConfig, footprint_score, transmit_allowed


@dataclass
class LowSlowConfig:
    window_seconds: float = 300.0        # sliding window the cap applies over
    max_weighted_per_window: int = 14    # footprint-weight ceiling per window ("quiet")
    min_gap_seconds: float = 8.0         # floor spacing between emissions
    jitter_seconds: float = 6.0          # random extra spacing so cadence isn't a pattern
    target_footprint: str = "quiet"      # informational: the posture this cap aims at


@dataclass
class Decision:
    allowed: bool
    wait_seconds: float
    reason: str
    window_weight: int = 0
    footprint_label: str = "silent"

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "wait_seconds": round(self.wait_seconds, 2),
                "reason": self.reason, "window_weight": self.window_weight,
                "footprint": self.footprint_label}


def _weight(kind: str, frames: int) -> int:
    return _EVENT_WEIGHT.get((kind or "").lower(), 1) * max(1, int(frames or 1))


@dataclass
class LowAndSlowScheduler:
    """Decide when the next emission may fire so activity stays low-and-slow."""
    config: LowSlowConfig = field(default_factory=LowSlowConfig)
    seed: int = 0
    _events: List[Tuple[float, int, str]] = field(default_factory=list)   # (ts, weight, kind)
    _next_allowed: float = 0.0
    _rng: random.Random = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def _prune(self, now: float) -> None:
        horizon = now - self.config.window_seconds
        self._events = [e for e in self._events if e[0] > horizon]   # strict: expire at the edge

    def window_weight(self, now: float) -> int:
        self._prune(now)
        return sum(w for _ts, w, _k in self._events)

    def _time_to_free(self, now: float, need: int) -> float:
        """Seconds until enough of the oldest weight ages out of the window to fit
        `need` more weight. Events expire window_seconds after they happened."""
        self._prune(now)
        freed = 0
        current = sum(w for _ts, w, _k in self._events)
        room = self.config.max_weighted_per_window - current
        if room >= need:
            return 0.0
        for ts, w, _k in sorted(self._events):          # oldest first
            freed += w
            if room + freed >= need:
                return max(0.0, (ts + self.config.window_seconds) - now)
        return self.config.window_seconds               # even a full flush isn't enough

    def permit(self, now: float, *, kind: str = "deauth", frames: int = 1,
               quiet: Optional[QuietConfig] = None, confirmed: bool = False) -> Decision:
        """May an emission of `kind` fire right now? Returns a glass-box Decision
        with the wait until it could, if not."""
        w = _weight(kind, frames)
        fp = footprint_score([{"kind": e[2], "frames": e[1] // max(1, _EVENT_WEIGHT.get(e[2], 1))}
                              for e in self._events]) if self._events else {"label": "silent"}
        cur = self.window_weight(now)
        # 1) Quiet mode gate — never loosened, only honoured.
        if quiet is not None:
            ok, why = transmit_allowed(quiet, confirmed=confirmed, frames_so_far=cur)
            if not ok:
                return Decision(False, self.config.window_seconds, f"quiet: {why}", cur, fp["label"])
        # 2) jittered minimum gap since the last emission
        if now < self._next_allowed:
            return Decision(False, self._next_allowed - now,
                            "min-gap/jitter not elapsed", cur, fp["label"])
        # 3) sliding-window weight cap
        if cur + w > self.config.max_weighted_per_window:
            wait = self._time_to_free(now, w)
            return Decision(False, wait,
                            f"window cap {self.config.max_weighted_per_window} reached "
                            f"({cur}+{w})", cur, fp["label"])
        return Decision(True, 0.0, f"clear — window {cur}+{w}/"
                        f"{self.config.max_weighted_per_window}", cur, fp["label"])

    def record(self, now: float, *, kind: str = "deauth", frames: int = 1) -> None:
        """Log an emission; set the next jittered gap so the cadence varies."""
        self._prune(now)
        self._events.append((now, _weight(kind, frames), (kind or "").lower()))
        gap = self.config.min_gap_seconds + self._rng.uniform(0.0, self.config.jitter_seconds)
        self._next_allowed = now + gap

    def next_slot(self, now: float, *, kind: str = "deauth", frames: int = 1,
                  quiet: Optional[QuietConfig] = None) -> float:
        """Earliest time an emission of `kind` would be permitted (for planning)."""
        d = self.permit(now, kind=kind, frames=frames, quiet=quiet)
        return now if d.allowed else now + d.wait_seconds

    def status(self, now: float) -> dict:
        cur = self.window_weight(now)
        fp = footprint_score([{"kind": k, "frames": w // max(1, _EVENT_WEIGHT.get(k, 1))}
                              for _ts, w, k in self._events])
        return {
            "window_seconds": self.config.window_seconds,
            "window_weight": cur,
            "cap": self.config.max_weighted_per_window,
            "headroom": max(0, self.config.max_weighted_per_window - cur),
            "footprint": fp,
            "target": self.config.target_footprint,
            "next_gap_at": round(self._next_allowed, 2),
            "note": (f"{cur}/{self.config.max_weighted_per_window} weighted frame(s) this "
                     f"window → {fp['label']}; pacing to stay {self.config.target_footprint}"),
        }
