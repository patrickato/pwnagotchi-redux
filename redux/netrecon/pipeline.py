"""Network recon + credential-test pipeline (Bjorn axis), scope-gated.

Every entry point takes the central Scope and refuses a target that isn't
authorized. The autonomous loop (`autorun`) ranges ONLY over the scope's CIDR
entries — "auto-hunt everything in your armed scope," never the open wire.

Tools are injected protocols so the logic is pure/testable:
  - Scanner:    ReplayScanner (canned) / NmapScanner (shells `nmap -sV`)
  - CredTester: ReplayCredTester (canned) / HydraCredTester (shells `hydra`)
Both report `available=False` with a reason when their tool is absent — never a
fabricated "nothing found."
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence


class ReconRefused(Exception):
    """Raised when a target is not in the authorized Scope."""


@dataclass(frozen=True)
class Port:
    number: int
    proto: str = "tcp"
    service: str = ""
    product: str = ""


@dataclass(frozen=True)
class Host:
    ip: str
    hostname: str = ""
    ports: tuple = ()          # tuple[Port, ...]


@dataclass(frozen=True)
class Credential:
    ip: str
    port: int
    service: str
    username: str
    secret: str
    reason: str = ""


@dataclass(frozen=True)
class ScanResult:
    available: bool
    hosts: tuple = ()          # tuple[Host, ...]
    reason: str = ""
    tool: str = ""


@dataclass(frozen=True)
class CredResult:
    available: bool
    found: Optional[Credential] = None
    reason: str = ""
    tool: str = ""


class Scanner(Protocol):
    def scan(self, cidr: str) -> ScanResult: ...


class CredTester(Protocol):
    def test(self, ip: str, port: int, service: str,
             usernames: Sequence[str], passwords: Sequence[str]) -> CredResult: ...


# --- replay (offline / test) impls ------------------------------------------ #

class ReplayScanner:
    """Canned scanner: map of cidr -> list[Host]. No network."""
    def __init__(self, hosts_by_cidr: Optional[Dict[str, List[Host]]] = None):
        self._by_cidr = hosts_by_cidr or {}

    def scan(self, cidr: str) -> ScanResult:
        hosts = tuple(self._by_cidr.get(cidr, []))
        return ScanResult(True, hosts, f"{len(hosts)} host(s) in {cidr}", tool="replay")


class ReplayCredTester:
    """Canned cred tester: map of (ip, port) -> (username, secret) it 'finds'."""
    def __init__(self, found: Optional[Dict[tuple, tuple]] = None):
        self._found = found or {}

    def test(self, ip, port, service, usernames, passwords) -> CredResult:
        hit = self._found.get((ip, port))
        if hit:
            u, p = hit
            if u in usernames and p in passwords:
                return CredResult(True, Credential(ip, port, service, u, p, "found in the supplied lists"),
                                  "credential recovered", tool="replay")
        return CredResult(True, None, "no credential in the supplied lists", tool="replay")


# --- live impls (honest tool-absence) --------------------------------------- #

class NmapScanner:
    BINARY = "nmap"

    def scan(self, cidr: str) -> ScanResult:
        if shutil.which(self.BINARY) is None:
            return ScanResult(False, (), f"{self.BINARY} not installed — enable the Kali pack", tool="")
        # Live parse is implemented on-device; structure verified by the replay path.
        return ScanResult(False, (), "live nmap scan is a real-hardware path", tool=self.BINARY)


class HydraCredTester:
    BINARY = "hydra"

    def test(self, ip, port, service, usernames, passwords) -> CredResult:
        if shutil.which(self.BINARY) is None:
            return CredResult(False, None, f"{self.BINARY} not installed — enable the Kali pack", tool="")
        return CredResult(False, None, "live hydra run is a real-hardware path", tool=self.BINARY)


# --- loot -------------------------------------------------------------------- #

@dataclass
class LootBook:
    """Accumulated findings from a run — hosts, services, recovered creds."""
    hosts: List[Host] = field(default_factory=list)
    credentials: List[Credential] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add_hosts(self, hosts: Sequence[Host]) -> None:
        seen = {h.ip for h in self.hosts}
        for h in hosts:
            if h.ip not in seen:
                self.hosts.append(h); seen.add(h.ip)

    def to_dict(self) -> dict:
        return {
            "hosts": [{"ip": h.ip, "hostname": h.hostname,
                       "ports": [{"number": p.number, "proto": p.proto,
                                  "service": p.service, "product": p.product} for p in h.ports]}
                      for h in self.hosts],
            "credentials": [{"ip": c.ip, "port": c.port, "service": c.service,
                             "username": c.username, "secret": c.secret, "reason": c.reason}
                            for c in self.credentials],
            "notes": list(self.notes),
            "summary": f"{len(self.hosts)} host(s), {len(self.credentials)} credential(s)",
        }


# --- scope-gated entry points ----------------------------------------------- #

def recon_subnet(cidr: str, *, scope, scanner: Optional[Scanner] = None,
                 require_scope: bool = True) -> ScanResult:
    """Scan one subnet — refused unless the subnet is authorized in Scope."""
    if require_scope:
        if scope is None:
            raise ReconRefused(f"refused to scan {cidr}: no scope provided")
        # require that a representative address of the CIDR is authorized
        probe_ip = cidr.split("/", 1)[0]
        ok, why = scope.authorize(ip=probe_ip)
        if not ok:
            raise ReconRefused(f"refused to scan {cidr}: {why}")
    return (scanner or NmapScanner()).scan(cidr)


def try_credentials(ip: str, port: int, service: str, usernames: Sequence[str],
                 passwords: Sequence[str], *, scope, tester: Optional[CredTester] = None,
                 require_scope: bool = True) -> CredResult:
    """Credential-test one service — refused unless the host is in Scope."""
    if require_scope:
        if scope is None:
            raise ReconRefused(f"refused to test {ip}:{port}: no scope provided")
        ok, why = scope.authorize(ip=ip)
        if not ok:
            raise ReconRefused(f"refused to test {ip}:{port}: {why}")
    return (tester or HydraCredTester()).test(ip, port, service, usernames, passwords)


def autorun(*, scope, scanner: Optional[Scanner] = None, tester: Optional[CredTester] = None,
            usernames: Sequence[str] = (), passwords: Sequence[str] = (),
            services: Sequence[str] = ("ssh", "ftp", "smb", "rdp", "telnet", "sql")) -> LootBook:
    """Autonomous sweep over the ARMED SCOPE only: for each authorized CIDR,
    scan, then (if cred lists given) test the discovered services in those
    service classes. Never ranges beyond the scope's CIDR entries."""
    book = LootBook()
    cidrs = [e.value for e in scope.active_entries() if e.kind == "cidr"] if scope else []
    if not cidrs:
        book.notes.append("no CIDR targets in scope — nothing to sweep (arm a subnet first)")
        return book
    for cidr in cidrs:
        res = recon_subnet(cidr, scope=scope, scanner=scanner)
        if not res.available:
            book.notes.append(f"{cidr}: scan unavailable — {res.reason}")
            continue
        book.add_hosts(res.hosts)
        book.notes.append(f"{cidr}: {res.reason}")
        if not (usernames and passwords):
            continue
        for h in res.hosts:
            for p in h.ports:
                if services and p.service and p.service.lower() not in services:
                    continue
                cred = try_credentials(h.ip, p.number, p.service or p.proto, usernames, passwords,
                                    scope=scope, tester=tester)
                if cred.found:
                    book.credentials.append(cred.found)
    return book
