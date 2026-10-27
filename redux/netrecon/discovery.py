"""LAN service discovery → a labeled inventory of what everything IS.

Device identification is only as good as the signals it gets. On a joined LAN the
richest signals are free: devices announce themselves over mDNS/DNS-SD
(_googlecast._tcp, _ipp._tcp, _hap._tcp …) and SSDP/UPnP, and DHCP/mDNS give their
hostnames. This gathers those, hands each host to identify(), and turns the subnet
into a map: "Chromecast @ .50, Echo @ .51, HP printer @ .52" — each with what you
can do to it.

The avahi/SSDP output parsers and the labeling are pure (verified with fixtures);
the live enumeration runs through an injected runner (avahi-browse / an SSDP
M-SEARCH) and is scope-gated — LAB runs it, FIELD needs authorization. Discovery
is active (it queries the LAN), so it's a firing path, not passive listening.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence


@dataclass
class DiscoveredHost:
    ip: str
    mac: str = ""
    hostname: str = ""
    name: str = ""                       # friendly/advertised name
    mdns_services: List[str] = field(default_factory=list)
    ssdp: str = ""

    def merge(self, **kw) -> None:
        for k, v in kw.items():
            if k == "mdns_service" and v:
                if v not in self.mdns_services:
                    self.mdns_services.append(v)
            elif v and not getattr(self, k, ""):
                setattr(self, k, v)


def parse_avahi_browse(text: str) -> Dict[str, DiscoveredHost]:
    """Parse `avahi-browse -rpt` resolved lines into hosts keyed by IP.
    Line: '=;iface;proto;Name;_type._tcp;domain;hostname;address;port;txt'."""
    hosts: Dict[str, DiscoveredHost] = {}
    for line in (text or "").splitlines():
        if not line.startswith("="):
            continue
        f = line.split(";")
        if len(f) < 8:
            continue
        name, svc, hostname, addr = f[3], f[4], f[6], f[7]
        if not addr:
            continue
        h = hosts.setdefault(addr, DiscoveredHost(ip=addr))
        h.merge(hostname=hostname, name=name, mdns_service=svc)
    return hosts


_SSDP_IP = re.compile(r"https?://(\d{1,3}(?:\.\d{1,3}){3})")


def parse_ssdp(text: str) -> Dict[str, DiscoveredHost]:
    """Parse SSDP M-SEARCH responses (blank-line-separated) into hosts by IP,
    capturing the SERVER string and the device type (ST/NT)."""
    hosts: Dict[str, DiscoveredHost] = {}
    for block in re.split(r"\r?\n\r?\n", text or ""):
        if not block.strip():
            continue
        ip = ""
        server = ""
        stype = ""
        for ln in block.splitlines():
            low = ln.lower()
            if low.startswith("location:"):
                m = _SSDP_IP.search(ln)
                if m:
                    ip = m.group(1)
            elif low.startswith("server:"):
                server = ln.split(":", 1)[1].strip()
            elif low.startswith("st:") or low.startswith("nt:"):
                stype = ln.split(":", 1)[1].strip()
        if ip:
            h = hosts.setdefault(ip, DiscoveredHost(ip=ip))
            h.merge(ssdp=(server + " " + stype).strip())
    return hosts


def _merge_hosts(*maps: Dict[str, DiscoveredHost]) -> List[DiscoveredHost]:
    merged: Dict[str, DiscoveredHost] = {}
    for m in maps:
        for ip, h in m.items():
            if ip not in merged:
                merged[ip] = DiscoveredHost(ip=ip)
            dst = merged[ip]
            dst.merge(mac=h.mac, hostname=h.hostname, name=h.name, ssdp=h.ssdp)
            for svc in h.mdns_services:
                dst.merge(mdns_service=svc)
    return sorted(merged.values(), key=lambda h: h.ip)


def label_host(host: DiscoveredHost) -> dict:
    """Run a discovered host through identify() → {ip, hostname, identity, actions}."""
    from ..intel.device_id import DeviceSignals, identify
    g = identify(DeviceSignals(
        mac=host.mac or "", hostname=host.hostname or host.name or "",
        mdns_services=tuple(host.mdns_services), ssdp=host.ssdp or ""))
    return {"ip": host.ip, "mac": host.mac, "hostname": host.hostname or host.name,
            "identity": g.to_dict()}


def label_inventory(hosts: Sequence[DiscoveredHost]) -> dict:
    """The labeled LAN map: every host + what it IS + what you can do to it."""
    rows = [label_host(h) for h in hosts or ()]
    identified = [r for r in rows if r["identity"]["category"] != "unknown"]
    return {"hosts": rows, "total": len(rows), "identified": len(identified),
            "note": (f"{len(identified)}/{len(rows)} host(s) identified on the LAN"
                     if rows else "no hosts discovered")}


@dataclass
class LanDiscovery:
    """Active mDNS + SSDP discovery over the joined LAN. runner injected for tests."""
    runner: Optional[Callable] = None

    def plan(self) -> List[str]:
        return ["avahi-browse -rpta   # resolve all mDNS/DNS-SD services",
                "SSDP M-SEARCH (ssdp:all) on 239.255.255.250:1900"]

    def discover(self, *, scope=None, authorized: bool = False, lab: bool = False,
                 avahi_raw: Optional[str] = None, ssdp_raw: Optional[str] = None,
                 now: Optional[float] = None) -> "tuple":
        """Gate → gather mDNS + SSDP → merge → label. Returns (inventory, reason)."""
        if not lab:
            if scope is not None:
                try:
                    ok, why = scope.authorize(ip=None, now=now)
                except Exception:
                    ok, why = False, "scope check failed"
                if not ok and not authorized:
                    return None, f"refused: {why} (LAN not armed / not --authorized)"
            elif not authorized:
                return None, "refused: active discovery needs --authorized in FIELD (or LAB)"
        if avahi_raw is None and ssdp_raw is None and self.runner is None:
            return None, "no discovery runner available (needs network access on-device)"
        mdns = parse_avahi_browse(avahi_raw or "")
        ssdp = parse_ssdp(ssdp_raw or "")
        hosts = _merge_hosts(mdns, ssdp)
        inv = label_inventory(hosts)
        return inv, inv["note"]
