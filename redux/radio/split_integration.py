"""Measured split planning and capture leases; no executor or automatic fallback."""
from dataclasses import dataclass, replace
import re
from typing import Sequence

from .dual_radio import RadioEvidence, SplitPlan, plan_dual_radio
from .orchestrator import Radio
from .probe import build_radios


@dataclass(frozen=True)
class MeasuredRadio:
    capabilities: Radio
    evidence: RadioEvidence
    ifindex: int
    transport: str = 'unknown'  # independently observed sysfs bus, not driver heuristic
    fresh: bool = False         # collector must bound age and observation consistency
    owner: str = "unknown"     # networkmanager | capture_exclusive | unknown

    def valid(self):
        r = self.capabilities
        return (isinstance(r, Radio) and isinstance(self.evidence, RadioEvidence)
                and type(self.ifindex) is int and self.ifindex > 0
                and isinstance(r.iface, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,15}', r.iface)
                and self.evidence.phy == r.phy and self.fresh is True
                and self.transport in ('usb', 'onboard')
                and type(r.monitor) is bool)


def plan_measured_split(measured: Sequence[MeasuredRadio], *, management_iface='wlan0',
                        ethernet_management_verified=False) -> SplitPlan:
    """Bind capability-probe records to fresh kernel identity and measured bus.

    Callers provide independent ownership/receive observations. Driver injection
    guesses are discarded, and unknown physical attachment blocks this bridge.
    Pass every netdev: duplicate PHYs must not be collapsed by a probe first.
    """
    records = tuple(measured)
    if (not all(isinstance(m, MeasuredRadio) and m.valid() for m in records)
            or len({m.ifindex for m in records}) != len(records)):
        return SplitPlan(management_iface, None, 'blocked', ('incomplete measured inventory',))
    radios = [replace(m.capabilities, onboard=m.transport == 'onboard', inject=False)
              for m in records]
    evidence = {m.capabilities.iface: replace(
        m.evidence, owner_verified=(m.evidence.owner_verified is True
            and m.owner == ("networkmanager" if m.transport == "onboard" else "capture_exclusive")))
        for m in records}
    return plan_dual_radio(radios, evidence,
                          management_iface=management_iface,
                          ethernet_management_verified=ethernet_management_verified)


@dataclass(frozen=True)
class CaptureLease:
    iface: str
    phy: str
    ifindex: int


def capture_lease(plan: SplitPlan, measured: Sequence[MeasuredRadio], *,
                  ethernet_management_verified=False) -> CaptureLease | None:
    # Recompute instead of trusting a stale or constructed SplitPlan.
    current = plan_measured_split(measured, management_iface=plan.management_iface,
                                 ethernet_management_verified=ethernet_management_verified)
    if (current.state != 'receive_verified' or current != plan):
        return None
    m = next(m for m in measured if m.capabilities.iface == plan.capture_iface)
    return CaptureLease(m.capabilities.iface, m.evidence.phy, m.ifindex)


def lease_present(lease: CaptureLease, measured: Sequence[MeasuredRadio], *,
                  management_verified: bool) -> bool:
    """False means stop capture owner before handoff; never choose a replacement.

    PAN and Ethernet are management-only. Interface-name reuse is insufficient;
    hotplug requires a newly reviewed lease after old owner termination/handoff.
    """
    if management_verified is not True:
        return False
    matches = [m for m in measured if m.capabilities.iface == lease.iface]
    return (all(isinstance(m, MeasuredRadio) and m.valid() for m in measured)
            and sum(m.evidence.phy == lease.phy for m in measured) == 1
            and len({m.ifindex for m in measured}) == len(measured)
            and len(matches) == 1 and matches[0].valid()
            and matches[0].transport == 'usb'
            and matches[0].owner == 'capture_exclusive'
            and matches[0].ifindex == lease.ifindex
            and matches[0].evidence.phy == lease.phy
            and matches[0].evidence.owner_verified is True
            and matches[0].evidence.mode == 'monitor'
            and type(matches[0].evidence.arphrd) is int
            and matches[0].evidence.arphrd in (801, 803))


def bind_probe_observations(iw_phy_text, iw_dev_text, metadata, observations, *,
                            owners=None, fresh=False):
    """Bind raw read-only iw snapshots and per-iface measured sysfs metadata.

    Preserve *all* netdevs, including multiple VIFs on one PHY. The legacy
    capability probe selects one netdev per PHY; using it alone would conceal
    an ownership conflict. Metadata supplies measured transport/driver/USB
    generation; ownership and decoded counts remain independent observations.
    """
    if (not isinstance(iw_phy_text, str) or not isinstance(iw_dev_text, str)
            or len(iw_phy_text) > 524288 or len(iw_dev_text) > 524288):
        raise ValueError('bounded iw observations required')
    caps = {r.phy: r for r in build_radios(iw_phy_text, iw_dev_text)}
    parsed = []
    phy = None
    current = None
    for line in iw_dev_text.splitlines():
        match = re.fullmatch(r'\s*phy#([0-9]+)\s*', line)
        if match:
            phy, current = 'phy' + match[1], None
            continue
        match = re.fullmatch(r'\s*Interface ([A-Za-z0-9_-]{1,15})\s*', line)
        if match and phy:
            current = dict(iface=match[1], phy=phy, ifindex=0, mode='unknown')
            parsed.append(current)
            if len(parsed) > 256:
                raise ValueError('interface inventory exceeds bound')
        elif current is not None:
            match = re.fullmatch(r'\s*ifindex ([0-9]+)\s*', line)
            if match:
                current['ifindex'] = int(match[1])
            match = re.fullmatch(r'\s*type (managed|monitor)\s*', line)
            if match:
                current['mode'] = match[1]
    result = []
    owners = owners or {}
    for item in parsed:
        name, phy = item['iface'], item['phy']
        capability = caps.get(phy, Radio(name, phy=phy))
        meta = metadata.get(name, {})
        evidence = observations.get(name, RadioEvidence(phy))
        observed = isinstance(evidence, RadioEvidence) and evidence.mode == item['mode']
        result.append(MeasuredRadio(
            replace(capability, iface=name, driver=meta.get('driver', ''),
                    usb_gen=meta.get('usb_gen'), inject=False),
            evidence, item['ifindex'], meta.get('transport', 'unknown'),
            fresh is True and observed and phy in caps, owners.get(name, "unknown")))
    return result
