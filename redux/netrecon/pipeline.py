"""Network recon + credential-test pipeline (Bjorn axis), scope-gated.

Every entry point takes the central Scope and refuses a target that isn't
authorized. The autonomous loop (`autorun`) ranges ONLY over the scope's CIDR
entries — "auto-hunt everything in your armed scope," never the open wire.

Tools are injected protocols so the logic is pure/testable:
  - Scanner:    ReplayScanner (canned, tests) / NmapScanner (real `nmap -sV`)
  - CredTester: ReplayCredTester (canned, tests) / HydraCredTester (real `hydra`)
The live executors run the real tool over an ALREADY scope-authorized target
(the scope gate is in `recon_subnet` / `try_credentials`, above these), parse the
tool's structured output, and fail honestly on every path (tool absent / invalid
target / timeout / tool error -> available=False) — never a fabricated host or
credential. HydraCredTester performs active credential guessing and only ever
runs against a host the operator armed in Scope.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence
from xml.etree import ElementTree as ET


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


# --- live impls (real tool execution + parse; honest on every failure) ------ #

# hydra service modules we allow as a `<module>://<ip>` target. Bounds the string
# that reaches the hydra argv (we never pass an unvetted service name through).
_HYDRA_SERVICES = frozenset({
    "ssh", "ftp", "ftps", "smb", "smb2", "rdp", "vnc", "telnet",
    "mysql", "postgres", "mssql", "oracle", "snmp", "ldap2", "ldap3",
    "smtp", "pop3", "imap", "redis", "mongodb", "http-get", "http-post",
    "https-get", "https-post", "http-head", "rlogin", "rsh", "svn",
})

_HYDRA_HIT = re.compile(
    r"\[\d+\]\[[^\]]+\]\s+host:\s*(\S+)\s+login:\s*(\S+)\s+password:\s*(.+?)\s*$")


def _valid_target(target: str) -> bool:
    """Accept only a real CIDR or single host address (never a flag/garbage)."""
    try:
        ipaddress.ip_network(target, strict=False)
        return True
    except (ValueError, TypeError):
        return False


def _parse_nmap_xml(xml: str) -> List[Host]:
    """Parse `nmap -oX` output into up-hosts with their open ports."""
    if not xml.strip():
        return []
    root = ET.fromstring(xml)
    hosts: List[Host] = []
    for hnode in root.findall("host"):
        status = hnode.find("status")
        if status is not None and status.get("state") not in (None, "up"):
            continue
        ip = ""
        for addr in hnode.findall("address"):
            if addr.get("addrtype") in ("ipv4", "ipv6"):
                ip = addr.get("addr", "")
                break
        if not ip:
            continue
        hn = hnode.find("hostnames/hostname")
        hostname = hn.get("name", "") if hn is not None else ""
        ports: List[Port] = []
        for pnode in hnode.findall("ports/port"):
            st = pnode.find("state")
            if st is None or st.get("state") != "open":
                continue
            try:
                num = int(pnode.get("portid", "0"))
            except (TypeError, ValueError):
                continue
            svc = pnode.find("service")
            ports.append(Port(
                number=num, proto=pnode.get("protocol", "tcp"),
                service=svc.get("name", "") if svc is not None else "",
                product=svc.get("product", "") if svc is not None else ""))
        hosts.append(Host(ip=ip, hostname=hostname, ports=tuple(ports)))
    return hosts


def _parse_hydra(out: str, ip: str, port: int, service: str) -> Optional[Credential]:
    for line in out.splitlines():
        m = _HYDRA_HIT.search(line)
        if m:
            return Credential(ip=ip, port=port, service=service,
                              username=m.group(2), secret=m.group(3),
                              reason="hydra recovered")
    return None


class NmapScanner:
    """Real `nmap -sV` service scan over an already scope-authorized CIDR.

    `recon_subnet` applies the scope gate before this runs; this only executes
    nmap on the vetted range and parses its XML. `runner` is injected so the
    parse is testable with no network."""

    BINARY = "nmap"

    def __init__(self, *, runner=subprocess.run, timeout: int = 900):
        self._run = runner
        self._timeout = timeout

    def scan(self, cidr: str) -> ScanResult:
        if shutil.which(self.BINARY) is None:
            return ScanResult(False, (), f"{self.BINARY} not installed — enable the Kali pack", tool="")
        if not _valid_target(cidr):
            return ScanResult(False, (), f"not a valid CIDR/host: {cidr!r}", tool=self.BINARY)
        argv = [self.BINARY, "-sV", "-Pn", "-T4", "--open", "-oX", "-", cidr]
        try:
            proc = self._run(argv, capture_output=True, text=True,
                             timeout=self._timeout, check=False)
        except subprocess.TimeoutExpired:
            return ScanResult(False, (), f"nmap timed out after {self._timeout}s", tool=self.BINARY)
        except (OSError, subprocess.SubprocessError) as error:
            return ScanResult(False, (), f"nmap failed: {type(error).__name__}", tool=self.BINARY)
        try:
            hosts = _parse_nmap_xml(getattr(proc, "stdout", "") or "")
        except ET.ParseError:
            return ScanResult(False, (), "nmap produced unparseable output", tool=self.BINARY)
        return ScanResult(True, tuple(hosts), f"{len(hosts)} host(s) in {cidr}", tool=self.BINARY)


class HydraCredTester:
    """Real `hydra` credential test against an already scope-authorized host.

    Gated upstream by `try_credentials` (scope.authorize(ip=)). This is active
    credential guessing and only ever reaches here for a host the operator armed
    in Scope. `runner` is injected for testing. A clean run with no hit returns
    available=True / found=None — never a fabricated credential."""

    BINARY = "hydra"

    def __init__(self, *, runner=subprocess.run, timeout: int = 1800):
        self._run = runner
        self._timeout = timeout

    def test(self, ip, port, service, usernames, passwords) -> CredResult:
        if shutil.which(self.BINARY) is None:
            return CredResult(False, None, f"{self.BINARY} not installed — enable the Kali pack", tool="")
        try:
            ipaddress.ip_address(ip)
        except (ValueError, TypeError):
            return CredResult(False, None, f"not a valid host IP: {ip!r}", tool=self.BINARY)
        if not (isinstance(port, int) and 1 <= port <= 65535):
            return CredResult(False, None, f"invalid port: {port!r}", tool=self.BINARY)
        module = str(service or "").lower()
        if module not in _HYDRA_SERVICES:
            return CredResult(False, None, f"unsupported hydra service: {service!r}", tool=self.BINARY)
        users = [u for u in usernames if u and "\n" not in u and "\r" not in u]
        secrets = [p for p in passwords if p and "\n" not in p and "\r" not in p]
        if not users or not secrets:
            return CredResult(False, None, "no usernames/passwords supplied", tool=self.BINARY)
        with tempfile.TemporaryDirectory(prefix="redux-hydra-") as tmp:
            upath, ppath = Path(tmp) / "u.txt", Path(tmp) / "p.txt"
            upath.write_text("\n".join(users) + "\n")
            ppath.write_text("\n".join(secrets) + "\n")
            argv = [self.BINARY, "-L", str(upath), "-P", str(ppath),
                    "-s", str(port), "-f", "-I", "-t", "4", f"{module}://{ip}"]
            try:
                proc = self._run(argv, capture_output=True, text=True,
                                 timeout=self._timeout, check=False)
            except subprocess.TimeoutExpired:
                return CredResult(False, None, f"hydra timed out after {self._timeout}s", tool=self.BINARY)
            except (OSError, subprocess.SubprocessError) as error:
                return CredResult(False, None, f"hydra failed: {type(error).__name__}", tool=self.BINARY)
        cred = _parse_hydra(getattr(proc, "stdout", "") or "", ip, port, module)
        if cred is not None:
            return CredResult(True, cred, "credential recovered", tool=self.BINARY)
        return CredResult(True, None, "no credential found in supplied lists", tool=self.BINARY)


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
        # The WHOLE requested network must be contained in an armed CIDR — not
        # merely have its first address fall inside one (which would let a broad
        # range pass and be swept in full).
        ok, why = scope.authorize_network(cidr)
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
