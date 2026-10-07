"""Sentinel mode — deploy-and-watch guardian.

The blue/purple product that falls out of parts redux already has: point the
detector suite + CSI presence-sensing at a space, and when something fires, route
a glass-box alert out over a notifier (LoRa in the field, a log/webhook anywhere).
Drop it in the shop, arm it, walk away — it tells you if someone deauths the air,
stands up an evil twin, drops a tracker, or just *moves in the room while you're
gone*.

It is a consumer, not a new detector: it normalizes alerts the detector suite
already emits (duck-typed, so it never reaches into that lane) and CSI readings
from the sense engine, classifies severity, de-duplicates a chattering signature,
and dispatches. Honest throughout:

  - **Armed vs home.** CSI motion only alerts when *armed* (you've left). At home
    it's suppressed, not fired — no crying wolf at your own footsteps.
  - **UNKNOWN never alerts.** An uncalibrated/warming CSI reading is ignored, never
    treated as motion.
  - **De-dup is glass-box.** A repeating signature is collapsed within a window and
    the re-fire carries its repeat count, rather than spamming identical alerts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


_ORDER = {Severity.INFO: 0, Severity.WARN: 1, Severity.CRITICAL: 2}
_SEV = {"critical": Severity.CRITICAL, "warning": Severity.WARN, "warn": Severity.WARN,
        "info": Severity.INFO}


@dataclass(frozen=True)
class SentinelEvent:
    ts: float
    source: str                 # e.g. "detector:rogue_ap", "csi", "tracker"
    severity: Severity
    summary: str
    reason: str
    key: str                    # dedup signature
    repeat: int = 1             # how many of this signature collapsed into this dispatch

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["severity"] = self.severity.value
        return d


def _normalize_alert(alert) -> SentinelEvent:
    kind = getattr(getattr(alert, "kind", None), "value", None) or str(getattr(alert, "kind", "alert"))
    sev = _SEV.get(str(getattr(alert, "severity", "warning")).lower(), Severity.WARN)
    bssid = getattr(alert, "bssid", "") or ""
    ssid = getattr(alert, "ssid", "") or ""
    ident = ssid or bssid or "?"
    return SentinelEvent(
        ts=float(getattr(alert, "ts", 0.0) or 0.0),
        source=f"detector:{kind}", severity=sev,
        summary=f"{kind} ({ident})", reason=getattr(alert, "reason", "") or kind,
        key=f"{kind}:{(bssid or ssid).lower()}")


class CollectingNotifier:
    """Default notifier — records dispatched events (log / test / stand-in for a
    LoRa sender, which is just an injected callable in the field)."""
    def __init__(self):
        self.sent: List[SentinelEvent] = []

    def send(self, event: SentinelEvent) -> None:
        self.sent.append(event)


class CallableNotifier:
    """Wrap any `fn(event_dict)` — e.g. a LoRa mesh send — as a notifier."""
    def __init__(self, fn: Callable[[dict], None]):
        self._fn = fn

    def send(self, event: SentinelEvent) -> None:
        self._fn(event.to_dict())


@dataclass
class Sentinel:
    notifier: object = field(default_factory=CollectingNotifier)
    min_severity: Severity = Severity.WARN
    dedup_window: float = 60.0           # seconds a repeating signature is collapsed
    armed: bool = False                  # armed = you've left; CSI motion then alerts
    _last: Dict[str, float] = field(default_factory=dict)       # key -> last dispatch ts
    _pending_repeat: Dict[str, int] = field(default_factory=dict)
    history: List[SentinelEvent] = field(default_factory=list)
    suppressed: int = 0

    # --- arm / disarm ------------------------------------------------------ #

    def arm(self) -> None:
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    # --- ingest ------------------------------------------------------------ #

    def observe_alert(self, alert, *, now: Optional[float] = None) -> Optional[SentinelEvent]:
        return self._consider(_normalize_alert(alert), now=now)

    def observe_motion(self, reading, *, now: Optional[float] = None) -> Optional[SentinelEvent]:
        sense = getattr(reading, "sense", None)
        sval = getattr(sense, "value", None) or str(sense)
        if sval != "motion":
            return None                   # UNKNOWN / still never alerts
        if not self.armed:
            self.suppressed += 1          # you're home — presence is expected
            return None
        ts = now if now is not None else getattr(reading, "ts", 0.0)
        ev = SentinelEvent(float(ts or 0.0), "csi", Severity.CRITICAL,
                           "motion detected while armed",
                           getattr(reading, "reason", "CSI motion above quiet baseline"),
                           "csi:motion")
        return self._consider(ev, now=now)

    def observe_tracker(self, ident: str, *, label: str = "", now: Optional[float] = None) -> Optional[SentinelEvent]:
        ts = now if now is not None else 0.0
        ev = SentinelEvent(float(ts or 0.0), "tracker", Severity.CRITICAL,
                           f"tracker following you: {label or ident}",
                           "a BLE tracker has persisted with you across locations",
                           f"tracker:{ident.lower()}")
        return self._consider(ev, now=now)

    # --- dispatch ---------------------------------------------------------- #

    def _consider(self, ev: SentinelEvent, *, now: Optional[float] = None) -> Optional[SentinelEvent]:
        now = ev.ts if now is None else now
        if _ORDER[ev.severity] < _ORDER[self.min_severity]:
            self.suppressed += 1
            return None
        last = self._last.get(ev.key)
        if last is not None and (now - last) < self.dedup_window:
            self._pending_repeat[ev.key] = self._pending_repeat.get(ev.key, 1) + 1
            self.suppressed += 1
            return None                   # collapsed into the window
        repeat = self._pending_repeat.pop(ev.key, 1)
        dispatched = SentinelEvent(ev.ts, ev.source, ev.severity, ev.summary, ev.reason,
                                   ev.key, repeat=repeat)
        self._last[ev.key] = now
        self.history.append(dispatched)
        self.notifier.send(dispatched)
        return dispatched

    # --- observe ----------------------------------------------------------- #

    def status(self) -> dict:
        counts = {s.value: sum(1 for e in self.history if e.severity is s) for s in Severity}
        last = self.history[-1] if self.history else None
        return {
            "armed": self.armed,
            "min_severity": self.min_severity.value,
            "dispatched": len(self.history),
            "suppressed": self.suppressed,
            "by_severity": counts,
            "last": last.to_dict() if last else None,
        }
