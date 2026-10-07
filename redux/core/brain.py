"""Glass-box brain — orchestration intelligence, honestly scoped (task 2.3).

What this brain is NOT: a channel-yield reinforcement learner. We proved in sim
that a learning policy does not reliably beat greedy+random at channel selection
(channel capture is restless/depleting; dumb coverage wins), so we don't sell
"more handshakes." What this brain IS: the orchestration decisions greedy never
made — *when* to hunt vs rest, *whether* an area is worth staying in, *how* to
respond to power — each with a plain-English reason you can read.

It observes the signal bus (captures, new APs, alerts, battery) over a rolling
window and, on demand, recommends an intent with a reason and a confidence. It is
**advisory**: it suggests; the Supervisor or operator decides. It never fires.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

from ..radio import Intent
from .signals import Signal


@dataclass(frozen=True)
class Decision:
    intent: Optional[Intent]   # suggested intent, or None = keep current
    reason: str                # glass-box: why
    confidence: float          # 0..1
    keep: bool = False         # True = "stay the course"


# tunables (seconds / counts) — module-level so they're visible and overridable
WINDOW_S = 600.0            # rolling activity window (10 min)
PRODUCTIVE_CAPTURES = 2     # captures in-window that mean "this spot is working"
QUIET_NEW_APS = 1           # fewer new APs than this in-window = "going quiet"
LOW_BATTERY = 20.0          # percent: conserve below this
CRITICAL_BATTERY = 8.0      # percent: rest now


class Brain:
    """Rolling-window orchestration advisor. Feed it bus emissions (or call the
    observe_* helpers); ask `recommend()` for a glass-box intent suggestion."""

    def __init__(self, clock=time.time, window_s: float = WINDOW_S):
        self._clock = clock
        self._window = window_s
        self._captures: Deque[float] = deque()
        self._new_aps: Deque[float] = deque()
        self._alerts: Deque[float] = deque()
        self._battery: Optional[float] = None
        self._charging: bool = False

    # --- intake ------------------------------------------------------------ #

    def observe(self, em) -> None:
        """Bus subscriber entry point (subscribe to WILDCARD)."""
        sig = getattr(em, "signal", "")
        p = getattr(em, "payload", {}) or {}
        if sig == Signal.EVENT.value:
            ev = p.get("event")
            etype = getattr(ev, "type", "")
            if etype == "handshake":
                self._captures.append(self._clock())
            elif etype == "ap.new":
                self._new_aps.append(self._clock())
        elif sig == Signal.ALERT.value:
            self._alerts.append(self._clock())
        elif sig == Signal.BATTERY.value:
            self._battery = _as_float(p.get("percent"))
            self._charging = bool(p.get("charging", False))

    def observe_battery(self, percent: float, charging: bool = False) -> None:
        self._battery = _as_float(percent)
        self._charging = charging

    # --- recommendation ---------------------------------------------------- #

    def _prune(self) -> None:
        cutoff = self._clock() - self._window
        for dq in (self._captures, self._new_aps, self._alerts):
            while dq and dq[0] < cutoff:
                dq.popleft()

    def recommend(self, current: Optional[Intent] = None) -> Decision:
        """Glass-box intent recommendation. Order matters: safety/power first,
        then productivity, then quiet-area, then default to staying put."""
        self._prune()
        caps, aps = len(self._captures), len(self._new_aps)

        # 1) power — overrides everything
        if self._battery is not None and not self._charging:
            if self._battery <= CRITICAL_BATTERY:
                return Decision(Intent.RECON, f"battery critical ({self._battery:.0f}%) — "
                                "drop to passive recon to stay alive", 0.95)
            if self._battery <= LOW_BATTERY:
                return Decision(Intent.RECON, f"battery low ({self._battery:.0f}%) — "
                                "conserve: passive recon, no active capture", 0.8)

        # 2) productive spot — keep hunting
        if caps >= PRODUCTIVE_CAPTURES:
            return Decision(Intent.HUNT, f"{caps} captures in the last "
                            f"{int(self._window/60)} min — productive spot, keep hunting", 0.75,
                            keep=(current == Intent.HUNT))

        # 3) going quiet — suggest a change (move / survey)
        if aps <= QUIET_NEW_APS and caps == 0:
            return Decision(Intent.SURVEY, f"only {aps} new AP(s) and no captures in "
                            f"{int(self._window/60)} min — area looks tapped out, suggest moving / survey", 0.6)

        # 4) default — stay the course
        return Decision(current, "steady activity — no change recommended", 0.5, keep=True)


def attach(bus, brain: Brain):
    """Subscribe a Brain to a SignalBus (wildcard). Returns the unsubscribe fn."""
    return bus.on("*", brain.observe)


def _as_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
