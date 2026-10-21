"""Radio Orchestrator — the headline feature.

You never touch monitor mode again. You declare an *intent* (online / hunt /
recon / survey) and the orchestrator maps whatever radios are present to roles
automatically, promotes a better adapter when you plug one in, and falls back
when you unplug it. Every decision carries a human-readable reason (glass-box).

Capability scaling is the contract: the device is complete on the onboard radio
alone (it captures), and every radio you add *strictly increases* capability —
a capable external adapter takes over active capture while the onboard stays in
the hunt as a second, passive radio on a complementary band (tandem), widening
the capture surface instead of sitting idle. Unplug it and capture falls
straight back to the onboard; it never goes dark.

This module is the pure decision engine — no OS calls — so it is fully unit
tested here. The live layer (iw-phy capability probe, udev hotplug, handing the
chosen interface to the capture engine) wires onto `decide()` / `on_hotplug()` /
`on_unplug()`; see TASKS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional


class Role(str, Enum):
    UPLINK = "uplink"        # managed client, internet
    CAPTURE = "capture"      # monitor mode, primary engine (active when able)
    SCAN = "scan"            # monitor mode, passive secondary capture (tandem)
    BLUETOOTH = "bluetooth"
    IDLE = "idle"


class Intent(str, Enum):
    ONLINE = "online"        # stay connected; no hunting
    HUNT = "hunt"            # active capture (needs injection) + tandem if able
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
    capture_mode: dict = field(default_factory=dict)   # iface -> "active"|"passive"
    capture_band: dict = field(default_factory=dict)   # iface -> band each capturer works


_BAND_RANK = {"6": 3, "5": 2, "2.4": 1}


def _capture_score(r: Radio) -> tuple:
    """Higher is a better capture radio. Prefers monitor, injection, more bands."""
    return (r.monitor, r.inject, "6" in r.bands, "5" in r.bands, bool(r.driver))


def _best_band(r: Radio) -> str:
    return max(r.bands, key=lambda b: _BAND_RANK.get(b, 0)) if r.bands else "2.4"


def _pick_band(r: Radio, taken: set) -> str:
    """Prefer a band this radio has that no higher-priority radio already works,
    so tandem radios spread across the spectrum instead of fighting one channel."""
    free = [b for b in r.bands if b not in taken]
    if free:
        return max(free, key=lambda b: _BAND_RANK.get(b, 0))
    return _best_band(r)


def _best(radios: Iterable[Radio], key: Callable, needed: Optional[Callable] = None) -> Optional[Radio]:
    cands = [r for r in radios if (needed is None or needed(r))]
    if not cands:
        return None
    return sorted(cands, key=key, reverse=True)[0]


def decide(radios: Iterable[Radio], intent, *, keep_uplink: bool = False) -> Assignment:
    """Map present radios to roles for an intent (glass-box).

    keep_uplink: in a capture intent, hold one managed uplink (preferring the
    onboard) for internet/sync instead of enlisting it as a tandem capture
    radio. Default False — the device prefers maximum capture surface and
    expects management over Bluetooth/USB-gadget. The live config may flip this
    per user preference without any code change here.
    """
    radios = list(radios)
    intent = Intent(intent)
    roles = {r.iface: Role.IDLE for r in radios}
    reasons: dict = {}
    warnings: list = []
    capture_mode: dict = {}
    capture_band: dict = {}

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
        return Assignment(roles, reasons, warnings, capture_mode, capture_band)

    # HUNT / RECON / SURVEY all need a capture radio.
    need_inject = intent is Intent.HUNT
    passive_intent = intent in (Intent.RECON, Intent.SURVEY)

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
        return Assignment(roles, reasons, warnings, capture_mode, capture_band)

    # Primary capture radio: active when this intent wants injection and it can.
    roles[cap.iface] = Role.CAPTURE
    taken: set = set()
    pband = _best_band(cap)
    taken.add(pband)
    active = bool(need_inject and cap.inject)
    capture_mode[cap.iface] = "active" if active else "passive"
    capture_band[cap.iface] = pband
    reasons[cap.iface] = (
        f"capture: primary {'active inject' if active else 'passive'} radio on {pband}GHz "
        f"for {intent.value}" + (" (passive)" if passive_intent else ""))

    if cap.high_draw and cap.usb_gen == 2:
        warnings.append(
            f"{cap.iface}: high-draw adapter on USB 2.0 — move it to a dedicated USB 3 port "
            f"to avoid brownout / clear-tt faults")

    # Everything else: enlist monitor-capable radios as tandem passive capture on
    # a complementary band; a radio that can't monitor gives internet if onboard.
    others = [r for r in radios if r.iface != cap.iface]
    uplink_iface = None
    if keep_uplink:
        u = _best(others, key=lambda r: (r.onboard, "5" in r.bands))
        if u is not None:
            uplink_iface = u.iface

    for r in sorted(others, key=_capture_score, reverse=True):
        if r.iface == uplink_iface:
            roles[r.iface] = Role.UPLINK
            reasons[r.iface] = "uplink: kept online during capture (management link)"
            continue
        if r.monitor:
            band = _pick_band(r, taken)
            taken.add(band)
            roles[r.iface] = Role.SCAN
            capture_mode[r.iface] = "passive"
            capture_band[r.iface] = band
            who = "onboard" if r.onboard else "spare"
            reasons[r.iface] = (
                f"capture(tandem): passive on {band}GHz — {who} radio widens the surface "
                f"alongside {cap.iface}")
        elif r.onboard:
            roles[r.iface] = Role.UPLINK
            reasons[r.iface] = (
                "uplink: no monitor mode here — provides internet/sync while capture runs")
        else:
            reasons[r.iface] = "idle: spare radio (no monitor mode)"

    for r in radios:
        reasons.setdefault(r.iface, "idle: spare radio")
    return Assignment(roles, reasons, warnings, capture_mode, capture_band)


def on_hotplug(current_radios: Iterable[Radio], new_radio: Radio, intent, **kw) -> Assignment:
    """A radio was plugged in. Recompute; `decide` handles promotion automatically."""
    return decide(list(current_radios) + [new_radio], intent, **kw)


def on_unplug(remaining_radios: Iterable[Radio], intent, **kw) -> Assignment:
    """A radio was removed. Recompute; `decide` handles graceful fallback."""
    return decide(list(remaining_radios), intent, **kw)
