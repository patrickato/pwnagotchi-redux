"""Resource Governor — one mechanism for heat, power, and SD-write load.

The device is a Pi 4/5 with a small TFT in a pocket or on a strap. When it gets
hot, or runs on battery, or the CPU/RAM is pegged, the Governor sheds load by
moving through four modes and emitting an `interval_scale` that stretches how
often the rest of the system does periodic work — pump cycles, telemetry
sampling, and (the big one) flushing sightings to the SD card. So "we're hot" and
"we're on battery" automatically become "scan slower, sample less, write less,"
with one knob, instead of each subsystem guessing.

Thresholds are tuned for the Pi 4 + 3.5" TFT reference (carried over from the
sibling Beastagotchi platform, same hardware). Escalation is immediate; recovery
is held for a cooldown so the device doesn't oscillate at a threshold edge.

Honest by construction: a reading that is unavailable (None) is simply not a
basis to shed on — it never reads as 0 and never fabricates a hot/empty value.
Every decision carries a human-readable reason (glass-box).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class Mode(str, Enum):
    FULL = "full"
    GUARDED = "guarded"
    REDUCED = "reduced"
    SURVIVAL = "survival"


_RANK = {Mode.FULL: 0, Mode.GUARDED: 1, Mode.REDUCED: 2, Mode.SURVIVAL: 3}
_ORDER = [Mode.FULL, Mode.GUARDED, Mode.REDUCED, Mode.SURVIVAL]

# Pi 4/5 + 3.5" TFT tuned thresholds (higher is worse)
TEMP_C = {Mode.GUARDED: 70.0, Mode.REDUCED: 76.0, Mode.SURVIVAL: 80.0}
LOAD_PCT = {Mode.GUARDED: 75.0, Mode.REDUCED: 90.0, Mode.SURVIVAL: 97.0}
RAM_PCT = {Mode.GUARDED: 76.0, Mode.REDUCED: 86.0, Mode.SURVIVAL: 94.0}
# battery % is lower-is-worse, and only applies when actually on battery
BATTERY_PCT = {Mode.GUARDED: 25.0, Mode.REDUCED: 15.0, Mode.SURVIVAL: 7.0}

INTERVAL_SCALE = {Mode.FULL: 1.0, Mode.GUARDED: 1.0, Mode.REDUCED: 1.5, Mode.SURVIVAL: 2.5}
RECOVERY_HOLD_S = 20.0


@dataclass(frozen=True)
class Reading:
    """A snapshot of real sensor state. Any field left None is 'unavailable' and
    is not used to escalate — never treated as zero."""
    cpu_temp_c: Optional[float] = None
    cpu_load_pct: Optional[float] = None
    ram_pct: Optional[float] = None
    battery_pct: Optional[float] = None
    on_battery: bool = False


@dataclass(frozen=True)
class GovDecision:
    mode: Mode
    interval_scale: float
    reason: str
    drivers: Tuple[str, ...] = ()       # which axes are at/over the current mode
    holding: bool = False               # recovering, waiting out the cooldown


def _rising_mode(thresholds, value: Optional[float]) -> Mode:
    """Worst mode a higher-is-worse axis reaches (FULL if unavailable)."""
    if value is None:
        return Mode.FULL
    worst = Mode.FULL
    for m in (Mode.GUARDED, Mode.REDUCED, Mode.SURVIVAL):
        if value >= thresholds[m]:
            worst = m
    return worst


def _falling_mode(thresholds, value: Optional[float]) -> Mode:
    """Worst mode a lower-is-worse axis (battery) reaches (FULL if unavailable)."""
    if value is None:
        return Mode.FULL
    worst = Mode.FULL
    for m in (Mode.GUARDED, Mode.REDUCED, Mode.SURVIVAL):
        if value <= thresholds[m]:
            worst = m
    return worst


@dataclass
class Governor:
    """Stateful: escalates immediately, eases down only after a sustained cooldown."""
    recovery_hold_s: float = RECOVERY_HOLD_S
    mode: Mode = Mode.FULL
    _cooldown_since: Optional[float] = field(default=None, repr=False)

    def _desired(self, r: Reading) -> Tuple[Mode, List[str]]:
        axes = [
            ("cpu_temp", _rising_mode(TEMP_C, r.cpu_temp_c), r.cpu_temp_c, "°C"),
            ("cpu_load", _rising_mode(LOAD_PCT, r.cpu_load_pct), r.cpu_load_pct, "%"),
            ("ram", _rising_mode(RAM_PCT, r.ram_pct), r.ram_pct, "%"),
        ]
        if r.on_battery:
            axes.append(("battery", _falling_mode(BATTERY_PCT, r.battery_pct), r.battery_pct, "%"))
        want = max((m for _, m, _, _ in axes), key=lambda m: _RANK[m])
        drivers = [f"{name} {val:g}{unit}" for name, m, val, unit in axes
                   if val is not None and _RANK[m] == _RANK[want] and want is not Mode.FULL]
        return want, drivers

    def evaluate(self, reading: Reading, now: float) -> GovDecision:
        want, drivers = self._desired(reading)
        holding = False
        if _RANK[want] >= _RANK[self.mode]:
            # escalate (or hold steady) immediately
            self.mode = want
            self._cooldown_since = None
        else:
            # want to ease — only after the cooldown has been sustained
            if self._cooldown_since is None:
                self._cooldown_since = now
            if now - self._cooldown_since >= self.recovery_hold_s:
                self.mode = want
                self._cooldown_since = None
            else:
                holding = True

        scale = INTERVAL_SCALE[self.mode]
        if self.mode is Mode.FULL:
            if any(reading.__dict__[k] is not None for k in ("cpu_temp_c", "cpu_load_pct", "ram_pct")) or reading.on_battery:
                reason = "FULL — within limits"
            else:
                reason = "FULL — no readings available, nothing to shed on"
        else:
            reason = f"{self.mode.value.upper()} — " + ", ".join(drivers or ["sustained load"])
            if holding:
                left = self.recovery_hold_s - (now - (self._cooldown_since or now))
                reason += f" (holding {left:.0f}s before easing)"
        return GovDecision(mode=self.mode, interval_scale=scale, reason=reason,
                           drivers=tuple(drivers), holding=holding)
