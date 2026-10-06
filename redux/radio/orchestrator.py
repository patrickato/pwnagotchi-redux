"""Radio Orchestrator — the headline feature.

You never touch monitor mode again. You declare an *intent* (online / hunt /
recon / survey) and the orchestrator maps whatever radios are present to roles
automatically, promotes a better adapter when you plug one in, and falls back
when you unplug it. Every decision carries a human-readable reason (glass-box).

This module is the pure decision engine — no OS calls — so it is fully unit
tested here. The live layer (iw-phy capability probe, udev hotplug, handing the
chosen interface to bettercap) wires onto `decide()` / `on_hotplug()` /
`on_unplug()`; see TASKS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional


class Role(str, Enum):
    UPLINK = "uplink"        # managed client, internet
    CAPTURE = "capture"      # monitor mode, handed to bettercap
    SCAN = "scan"            # passive discovery
    BLUETOOTH = "bluetooth"
    IDLE = "idle"


class Intent(str, Enum):
    ONLINE = "online"        # stay connected; no hunting
    HUNT = "hunt"            # active capture (needs injection) + keep uplink if possible
    RECON = "recon"          # passive monitor only, no injection
    SURVEY = "survey"        # passive monitor + GPS (wardrive/mapping)


@dataclass(frozen=True)
class Radio:
    iface: str
    phy: str = ""
    bands: frozenset = frozenset({"2.4"})      # subset of {"2.4","5","6"}
    monitor: bool = False
    inject: bool = False
    driver: str = ""
    onboard: bool = False
    usb_gen: Optional[int] = None              # 2 or 3 for USB; None = onboard
    high_draw: bool = False                    # 11ac/USB3 adapter prone to brownout


@dataclass
class Assignment:
    roles: dict                                # iface -> Role
    reasons: dict = field(default_factory=dict)   # iface -> why (glass-box)
    warnings: list = field(default_factory=list)


def _capture_score(r: Radio) -> tuple:
    """Higher is a better capture radio. Prefers monitor, injection, more bands."""
    return (r.monitor, r.inject, "6" in r.bands, "5" in r.bands, bool(r.driver))


def _best(radios: Iterable[Radio], key: Callable, needed: Optional[Callable] = None) -> Optional[Radio]:
    cands = [r for r in radios if (needed is None or needed(r))]
    if not cands:
        return None
    return sorted(cands, key=key, reverse=True)[0]


def decide(radios: Iterable[Radio], intent) -> Assignment:
    radios = list(radios)
    intent = Intent(intent)
    roles = {r.iface: Role.IDLE for r in radios}
    reasons: dict = {}
    warnings: list = []

    def monitor_capable(r: Radio) -> bool:
        return r.monitor

    def inject_capable(r: Radio) -> bool:
        return r.monitor and r.inject

    if intent is Intent.ONLINE:
        up = _best(radios, key=lambda r: (r.onboard, "5" in r.bands))
        if up:
            roles[up.iface] = Role.UPLINK
            reasons[up.iface] = "uplink: online intent; preferred onboard for the managed client"
        for r in radios:
            reasons.setdefault(r.iface, "idle: not needed while online")
        return Assignment(roles, reasons, warnings)

    # HUNT / RECON / SURVEY all need a capture radio.
    need_inject = intent is Intent.HUNT
    cap = _best(radios, key=_capture_score,
                needed=(inject_capable if need_inject else monitor_capable))
    if cap is None and need_inject:
        cap = _best(radios, key=_capture_score, needed=monitor_capable)
        if cap is not None:
            warnings.append(
                f"{cap.iface}: no injection-capable radio present — HUNT degraded to passive capture")
    if cap is None:
        warnings.append("no monitor-capable radio present — cannot capture")
        for r in radios:
            reasons.setdefault(r.iface, "idle: cannot capture")
        return Assignment(roles, reasons, warnings)

    roles[cap.iface] = Role.CAPTURE
    passive = intent in (Intent.RECON, Intent.SURVEY)
    reasons[cap.iface] = (
        f"capture: best {'inject+' if cap.inject else ''}monitor radio for {intent.value}"
        + (" (passive)" if passive else ""))

    if cap.high_draw and cap.usb_gen == 2:
        warnings.append(
            f"{cap.iface}: high-draw adapter on USB 2.0 — move it to a dedicated USB 3 port "
            f"to avoid brownout / clear-tt faults")

    others = [r for r in radios if r.iface != cap.iface]
    if intent in (Intent.HUNT, Intent.RECON):
        up = _best(others, key=lambda r: (r.onboard,))
        if up:
            roles[up.iface] = Role.UPLINK
            reasons[up.iface] = "uplink: kept online while the capture radio works"

    for r in radios:
        reasons.setdefault(r.iface, "idle: spare radio")
    return Assignment(roles, reasons, warnings)


def on_hotplug(current_radios: Iterable[Radio], new_radio: Radio, intent) -> Assignment:
    """A radio was plugged in. Recompute; `decide` handles promotion automatically."""
    return decide(list(current_radios) + [new_radio], intent)


def on_unplug(remaining_radios: Iterable[Radio], intent) -> Assignment:
    """A radio was removed. Recompute; `decide` handles graceful fallback."""
    return decide(list(remaining_radios), intent)
