"""Bounded volatile trends from Redux's existing measured live-status counters.

A dashboard plot is not proof of radiotap reception, valid captures, or
handshake authentication. There is no disk, device or network access here.
Only live engine status samples form a trend. This is never a replay buffer.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time

_MAX_COUNT = (1 << 63) - 1
_MAX_POINTS = 48
_MAX_AGE_SECONDS = 300
_MAX_INTERVAL_SECONDS = 45
_MAX_RUNTIME_LAG = 20
_KEYS = ("events_seen", "handshake_events", "handed_off")


def _counter(value):
    return value if type(value) is int and 0 <= value <= _MAX_COUNT else None


def _clock(value):
    if type(value) not in (int, float):
        return None
    try:
        if math.isfinite(value) and 0 <= value <= 1e12:
            return float(value)
    except (OverflowError, ValueError, TypeError):
        pass
    return None


@dataclass
class TelemetryWindow:
    """Per-process, session-scoped, fixed-size volatile derivative history."""

    samples: deque = field(default_factory=lambda: deque(maxlen=_MAX_POINTS))
    rates: deque = field(default_factory=lambda: deque(maxlen=_MAX_POINTS - 1))
    identity: tuple | None = None
    state: str = "NO SAMPLES"

    def clear(self, reason="NO SAMPLES"):
        self.samples.clear()
        self.rates.clear()
        self.identity = None
        self.state = reason

    def ingest(self, report, *, now=None):
        """Accept one independently fresh API sample, never inferred packets.

        Identical server timestamps are duplicates. Discontinuity, engine
        restart, counter rollback, malformed fields, or unavailable status
        discontinues the series rather than connecting unrelated readings.
        """
        now = time.time() if now is None else now
        moment = _clock(now)
        runtime = report.get("runtime") if isinstance(report, dict) else None
        if not isinstance(runtime, dict) or moment is None:
            self.clear("UNAVAILABLE")
            return False
        measured = _clock(runtime.get("updated_utc"))
        if (measured is None or not -30 <= moment - measured <= _MAX_RUNTIME_LAG
                or runtime.get("state") != "running"):
            self.clear("NOT CURRENT")
            return False
        iface = runtime.get("iface")
        pid = runtime.get("pid")
        if (not isinstance(iface, str) or not 1 <= len(iface) <= 15
                or not all(ch.isascii() and (ch.isalnum() or ch in "_.-") for ch in iface)
                or type(pid) not in (int, type(None)) or
                (pid is not None and not 1 <= pid <= _MAX_COUNT)):
            self.clear("UNQUALIFIED")
            return False
        counters = tuple(_counter(runtime.get(key)) for key in _KEYS)
        if any(value is None for value in counters):
            self.clear("COUNTERS UNKNOWN")
            return False
        identity = (iface, pid)
        if self.samples:
            prev = self.samples[-1]
            last_at, last_values = prev
            if identity != self.identity:
                self.clear("ENGINE CHANGED")
            elif measured == last_at:
                # Reject revised same-timestamp payloads; never create a
                # division-by-zero interval or overwrite a past observation.
                if counters != last_values:
                    self.clear("CLOCK CONFLICT")
                return False
            elif measured < last_at or measured - last_at > _MAX_INTERVAL_SECONDS:
                self.clear("SAMPLE GAP")
            elif any(n < p for n, p in zip(counters, last_values)):
                self.clear("COUNTER RESET")
        if self.samples:
            last_at, last_values = self.samples[-1]
            interval = measured - last_at
            rate = tuple((n - p) * 60.0 / interval
                         for n, p in zip(counters, last_values))
            if not all(math.isfinite(value) and value <= 1e12 for value in rate):
                self.clear("RATE INVALID")
            else:
                self.rates.append((measured, rate))
        self.samples.append((measured, counters))
        self.identity = identity
        if self.rates:
            self.state = "TRACKING"
        elif self.state not in ("ENGINE CHANGED", "CLOCK CONFLICT",
                                "SAMPLE GAP", "COUNTER RESET", "RATE INVALID"):
            self.state = "ONE SAMPLE"
        return True

    def snapshot(self, *, now=None):
        now = time.time() if now is None else now
        moment = _clock(now)
        if moment is None or not self.samples:
            return {"state": self.state if self.samples else "NO SAMPLES",
                    "points": [], "last": None, "seconds": None}
        last_at = self.samples[-1][0]
        if not -30 <= moment - last_at <= _MAX_RUNTIME_LAG:
            return {"state": "STALE", "points": [], "last": None, "seconds": None}
        # Never extend lines across old horizon edges.
        horizon = moment - _MAX_AGE_SECONDS
        series = [
            {"age_seconds": max(0, round(moment - at)),
             "events_per_min": round(rate[0], 2),
             "hs_events_per_min": round(rate[1], 2),
             "handoffs_per_min": round(rate[2], 2)}
            for at, rate in self.rates if at >= horizon
        ]
        if not series:
            return {"state": self.state, "points": [],
                    "last": None, "seconds": None}
        return {"state": "TRACKING", "points": series,
                "last": series[-1], "seconds": round(max(0, moment - self.samples[0][0]))}
