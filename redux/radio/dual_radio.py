"""Pure USB-capture / onboard-management plan: no system or RF mutations.

A capable adapter is a *candidate*, not a functioning capture interface.
Only externally measured ownership, 802.11 framing, decoded reception and
management continuity can qualify a split. No function here authorizes
activation, changes a device or overrides NetworkManager safety gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .orchestrator import Radio


@dataclass(frozen=True)
class RadioEvidence:
    """Read-only, independently obtained hardware observations for one netdev."""
    phy: str
    mode: str = "unknown"
    arphrd: int | None = None
    decoded_frames: int | None = None
    owner_verified: bool = False
    management_connected: bool = False


@dataclass(frozen=True)
class SplitPlan:
    management_iface: str
    capture_iface: str | None
    state: str                         # blocked | candidate | receive_verified
    reasons: tuple[str, ...]
    activation_authorized: bool = False  # always: execution is a separate reviewed gate
    management_path: str = "onboard_wifi"


def _valid(radio: Radio) -> bool:
    return (isinstance(radio, Radio) and isinstance(radio.iface, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", radio.iface) is not None
            and radio.iface not in (".", "..")
            and isinstance(radio.phy, str)
            and re.fullmatch(r"phy[0-9]+", radio.phy) is not None
            and type(radio.monitor) is bool and type(radio.onboard) is bool)


def plan_dual_radio(radios: Sequence[Radio],
                    observations: Mapping[str, RadioEvidence],
                    *, management_iface: str = "wlan0",
                    ethernet_management_verified: bool = False) -> SplitPlan:
    """Plan *only*: never take management down to gain capture.

    Refuse duplicate netdev/PHY identities and management data with uncertain
    ownership. Capture candidates MUST be non-onboard and on a distinct PHY;
    losing the external adapter does not silently promote the management radio.
    """
    devices = tuple(radios)
    def blocked(why: str) -> SplitPlan:
        return SplitPlan(management_iface, None, "blocked", (why,))
    if not all(_valid(r) for r in devices):
        return blocked("incomplete or invalid PHY/radio inventory")
    if len({r.iface for r in devices}) != len(devices):
        return blocked("duplicate interface identity")
    if len({r.phy for r in devices}) != len(devices):
        return blocked("multiple interfaces share a PHY; independent ownership is unproven")
    mgmt = next((r for r in devices if r.iface == management_iface), None)
    if mgmt is None or not mgmt.onboard:
        return blocked("onboard management radio is missing or unconfirmed")
    seen_mgmt = observations.get(management_iface)
    if not isinstance(seen_mgmt, RadioEvidence) or seen_mgmt.phy != mgmt.phy:
        return blocked("onboard management PHY identity not verified")
    onboard_connected = (seen_mgmt.mode == "managed"
                         and seen_mgmt.owner_verified is True
                         and seen_mgmt.management_connected is True)
    # Ethernet is accepted only when a separate strict route/SSH check has
    # *positively* proven that path. A false or missing claim never unlocks it.
    management_path = ("onboard_wifi" if onboard_connected else
                       "verified_ethernet" if ethernet_management_verified is True
                       else None)
    if management_path is None:
        return blocked("management path not independently verified")

    candidates = [r for r in devices if r.iface != mgmt.iface
                  and r.phy != mgmt.phy and not r.onboard and r.monitor]
    if not candidates:
        return blocked("no independent external monitor-capable radio")
    # Prefer actually qualified receive interfaces before driver heuristics;
    # remaining ties prefer measured USB3 and wider advertised bands.
    def qualified(r: Radio) -> bool:
        e = observations.get(r.iface)
        return (isinstance(e, RadioEvidence) and e.phy == r.phy
                and e.mode == "monitor" and e.owner_verified is True
                and type(e.arphrd) is int and e.arphrd in (801, 803)
                and type(e.decoded_frames) is int and e.decoded_frames > 0)
    candidates.sort(key=lambda r: (
        qualified(r), r.usb_gen == 3, "5" in r.bands, "6" in r.bands, r.iface
    ), reverse=True)
    capture = candidates[0]
    if not qualified(capture):
        return SplitPlan(management_iface, capture.iface, "candidate", (
            "external capture PHY separate; receive framing, decoded packets "
            "or ownership remain unverified",), management_path=management_path)
    return SplitPlan(management_iface, capture.iface, "receive_verified", (
        "independent management link and measured external 802.11 reception; "
        "capture handoff and persistence remain separate acceptance gates",),
        management_path=management_path)
