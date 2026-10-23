"""Measured, passive two-engine binding boundary (no subprocess or RF actions).

The broad orchestrator and capture-binding planner describe desired roles.
They do not establish that either radio can actually receive traffic, that
an engine owns it, or that two independently supervised writers exist.
This adapter accepts only the narrow Pi onboard Bettercap + external USB
AngryOxide arrangement, retaining observed netdev generations for the future
executor. Even ``radio_qualified`` never authorizes launch or transmission.
"""
from dataclasses import dataclass, replace
from typing import Sequence

from ..crack.capture import plan_capture_bindings
from ..radio.dual_radio import RadioEvidence
from ..radio.orchestrator import Intent, Role, decide
from ..radio.split_integration import (CaptureLease, MeasuredRadio,
                                       capture_lease, plan_measured_split)


@dataclass(frozen=True)
class MeasuredEngineBinding:
    engine: str
    iface: str
    phy: str
    ifindex: int
    role: str
    band_plan: str  # advertised/planned band, NOT a measured tuned channel
    passive: bool = True


@dataclass(frozen=True)
class TandemReadiness:
    state: str  # blocked | candidate | radio_qualified
    bindings: tuple[MeasuredEngineBinding, ...]
    usb_lease: CaptureLease | None
    reasons: tuple[str, ...]
    activation_authorized: bool = False

    def to_dict(self) -> dict:
        """Public preview: never publish process identity or authorization."""
        return {
            "state": self.state,
            "bindings": [{"engine": b.engine, "iface": b.iface,
                          "role": b.role, "band_plan": b.band_plan,
                          "passive": b.passive} for b in self.bindings],
            "reasons": list(self.reasons),
            "activation_authorized": False,
        }


def plan_measured_tandem(measured: Sequence[MeasuredRadio], *,
                         onboard_iface: str = "wlan0",
                         ethernet_management_verified: bool = False,
                         bettercap_present: bool = False,
                         angryoxide_present: bool = False) -> TandemReadiness:
    """Qualify radio identities for *future* two-process passive execution.

    Freshness, Ethernet and owner facts must originate from an independent
    collector. The future executor must separately pin processes, prove its
    capture mounts, and obtain operation-specific approval. Driver injection
    guesses are discarded; this bridge only permits passive binding.
    """
    def result(state, why):
        return TandemReadiness(state, (), None, (why,))

    records = tuple(measured)
    if (len(records) != 2 or not all(isinstance(m, MeasuredRadio) and m.valid()
                                     for m in records)
            or len({m.capabilities.iface for m in records}) != 2
            or len({m.ifindex for m in records}) != 2
            or len({m.evidence.phy for m in records}) != 2):
        return result("blocked", "two fresh, distinct radio identities required")
    if ethernet_management_verified is not True:
        return result("blocked", "Ethernet management path not independently verified")
    if bettercap_present is not True or angryoxide_present is not True:
        return result("blocked", "both capture engines must be positively available")

    onboard = next((m for m in records if m.capabilities.iface == onboard_iface), None)
    external = next((m for m in records if m.transport == "usb"), None)
    if (onboard is None or onboard.transport != "onboard"
            or external is None or external.capabilities.iface == onboard_iface):
        return result("blocked", "independent onboard and USB radios required")

    split = plan_measured_split(records, management_iface=onboard_iface,
                               ethernet_management_verified=True)
    if split.state != "receive_verified" or split.capture_iface != external.capabilities.iface:
        return result("candidate" if split.state == "candidate" else "blocked",
                      "; ".join(split.reasons))
    lease = capture_lease(split, records, ethernet_management_verified=True)
    if lease is None:
        return result("blocked", "fresh USB capture lease cannot be derived")

    observed = onboard.evidence
    if (not isinstance(observed, RadioEvidence) or observed.mode != "monitor"
            or type(observed.arphrd) is not int or observed.arphrd not in (801, 803)
            or type(observed.decoded_frames) is not int or observed.decoded_frames <= 0
            or onboard.owner != "capture_exclusive" or observed.owner_verified is not True):
        return result("candidate", "onboard Bettercap framing, decoded reception or exclusive ownership unverified")

    # Advertised injection support does not authorize active RF; the selected
    # radio and per-radio engines must both agree with the measured USB split.
    radios = [replace(m.capabilities, inject=False) for m in records]
    assignment = decide(radios, Intent.RECON)
    planned = plan_capture_bindings(assignment, angryoxide_present=True,
                                    bettercap_present=True)
    bindings = {b.iface: b for b in planned}
    if (len(planned) != 2 or set(bindings) != {onboard_iface, lease.iface}
            or assignment.roles.get(lease.iface) is not Role.CAPTURE
            or assignment.roles.get(onboard_iface) is not Role.SCAN
            or bindings[lease.iface].engine != "angryoxide"
            or bindings[onboard_iface].engine != "bettercap"
            or any(b.mode != "passive" for b in planned)):
        return result("blocked", "planner cannot bind one passive engine per measured radio")

    by_iface = {m.capabilities.iface: m for m in records}
    outcome = tuple(MeasuredEngineBinding(
        b.engine, b.iface, by_iface[b.iface].evidence.phy,
        by_iface[b.iface].ifindex, b.role, b.band)
        for b in (bindings[onboard_iface], bindings[lease.iface]))
    return TandemReadiness("radio_qualified", outcome, lease, (
        "passive radio measurements qualify a two-engine candidate; "
        "live process ownership, outputs and hardware approval remain open",))
