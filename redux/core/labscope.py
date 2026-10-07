"""Lab-mode auto-detect — seed the Scope from the device's *own* kit, zero typing.

`redux scope arm-lab` already pre-authorizes your gear in one gesture, but you
still type the BSSIDs/SSIDs/CIDRs. This closes that last gap: the device can read
its *own* network facts (the subnet its uplink sits on, its own radio MACs, an AP
it is itself broadcasting) and propose them as lab targets.

The safety line is strict and glass-box: **only things that are provably yours get
auto-armed.**
  - A CIDR is auto-armed only if it is a **private** range (RFC1918 / unique-local)
    the device is itself attached to — your lab LAN. A **public** uplink range is
    *proposed but not armed*: it could be your ISP's/carrier's space, not yours, so
    you add it explicitly if you own it.
  - Loopback / link-local are skipped.
  - Your own radios' MACs and an SSID this device is itself broadcasting are yours,
    so they auto-arm.
  - Discovered neighbours are **never** here — this reads the device's own
    interfaces, not the air. It can only ever propose your own equipment.

Every proposal carries a reason and an accept/skip decision, so `arm-lab --auto`
shows exactly what it armed and what it left for you to decide. Pure logic lives
here; the host collection (shelling `ip`/`iw`) is injected and honest about absent
tools.
"""
from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


@dataclass(frozen=True)
class LabFacts:
    """What the device knows about its *own* kit. Each item notes the interface it
    came from, so a proposal can explain itself."""
    cidrs: List[Tuple[str, str]] = field(default_factory=list)   # (cidr, iface)
    macs: List[Tuple[str, str]] = field(default_factory=list)    # (mac, iface)
    ssids: List[Tuple[str, str]] = field(default_factory=list)   # (ssid, iface) — AP we broadcast
    notes: List[str] = field(default_factory=list)               # collection caveats (honest absence)


@dataclass(frozen=True)
class LabProposal:
    kind: str            # bssid | ssid | cidr
    value: str
    accept: bool         # True → auto-armed; False → shown but left to the operator
    reason: str


def _cidr_proposal(cidr: str, iface: str) -> Optional[LabProposal]:
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None
    val = str(net)
    if net.is_loopback or net.is_link_local:
        return LabProposal("cidr", val, False, f"{iface} {val} is loopback/link-local — skipped")
    if net.is_private:   # RFC1918 v4, unique-local v6 → your lab LAN
        return LabProposal("cidr", val, True, f"your own subnet on {iface}")
    return LabProposal("cidr", val, False,
                       f"{iface} is on a public range ({val}) — not auto-armed; "
                       f"add it explicitly if you own it")


def propose_lab_scope(facts: LabFacts) -> List[LabProposal]:
    """Turn the device's own-kit facts into accept/skip scope proposals. Pure."""
    out: List[LabProposal] = []
    seen = set()

    def _emit(p: Optional[LabProposal]) -> None:
        if p is None:
            return
        key = (p.kind, p.value.lower())
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    for cidr, iface in facts.cidrs:
        _emit(_cidr_proposal(cidr, iface))
    for mac, iface in facts.macs:
        m = (mac or "").strip().lower()
        if m:
            _emit(LabProposal("bssid", m, True, f"this device's own radio ({iface})"))
    for ssid, iface in facts.ssids:
        s = (ssid or "").strip()
        if s:
            _emit(LabProposal("ssid", s, True, f"an AP this device broadcasts ({iface})"))
    return out


# --- host collection (injected, honest about absent tools) ------------------- #

def parse_ip_json(data) -> List[Tuple[str, str]]:
    """Parse `ip -json addr` output → [(cidr, iface)]. Skips the loopback iface."""
    out: List[Tuple[str, str]] = []
    for link in data or []:
        iface = link.get("ifname", "")
        if iface == "lo":
            continue
        for a in link.get("addr_info", []) or []:
            local = a.get("local")
            plen = a.get("prefixlen")
            if local is None or plen is None:
                continue
            out.append((f"{local}/{plen}", iface))
    return out


def parse_iw_dev(text: str) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Parse `iw dev` output → (macs, ssids). An SSID is taken only from an
    interface whose type is AP (a network we *broadcast*); a managed interface's
    joined network is someone else's and is ignored."""
    macs: List[Tuple[str, str]] = []
    ap_ssids: List[Tuple[str, str]] = []
    iface = ""
    mac = ""
    ssid = ""
    is_ap = False

    def _flush():
        nonlocal mac, ssid, is_ap
        if iface and mac:
            macs.append((mac, iface))
        if iface and is_ap and ssid:
            ap_ssids.append((ssid, iface))
        mac = ""
        ssid = ""
        is_ap = False

    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("Interface "):
            _flush()
            iface = line.split(None, 1)[1].strip()
        elif line.startswith("addr "):
            mac = line.split(None, 1)[1].strip()
        elif line.startswith("ssid "):
            ssid = line.split(None, 1)[1].strip()
        elif line.startswith("type "):
            is_ap = line.split(None, 1)[1].strip() == "AP"
    _flush()
    return macs, ap_ssids


def collect_lab_facts(runner: Optional[Callable[[List[str]], str]] = None) -> LabFacts:
    """Collect the device's own-kit facts from the host. `runner([...]) -> stdout`
    is injectable for tests; the default shells out. Missing tools are recorded as
    notes rather than raising — you still get whatever was available."""
    def _default(cmd: List[str]) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                              check=True).stdout
    run = runner or _default

    cidrs: List[Tuple[str, str]] = []
    macs: List[Tuple[str, str]] = []
    ssids: List[Tuple[str, str]] = []
    notes: List[str] = []

    try:
        cidrs = parse_ip_json(json.loads(run(["ip", "-json", "addr"])))
    except Exception as e:
        notes.append(f"could not read interface addresses (ip): {e}")
    try:
        macs, ssids = parse_iw_dev(run(["iw", "dev"]))
    except Exception as e:
        notes.append(f"could not read wireless interfaces (iw): {e}")

    return LabFacts(cidrs=cidrs, macs=macs, ssids=ssids, notes=notes)
