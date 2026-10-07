"""Network-layer recon axis — the second half of the kill-chain.

Once redux is on an authorized network, this is the IP-layer work the RF side
can't do: discover hosts and open ports, enumerate services, test credentials,
and record loot. It mirrors the Bjorn pattern — but every target must be in the
central Scope (CIDR entries), and the autonomous loop only ever ranges over the
scope you armed, never "whatever's on the wire." Tools are injected (nmap/hydra
on hardware; replay impls in tests), and tool-absence is reported honestly, not
as a fake negative.
"""
from .pipeline import (
    Port,
    Host,
    Credential,
    ScanResult,
    CredResult,
    Scanner,
    CredTester,
    ReplayScanner,
    ReplayCredTester,
    NmapScanner,
    HydraCredTester,
    LootBook,
    ReconRefused,
    recon_subnet,
    try_credentials,
    autorun,
)

__all__ = [
    "Port", "Host", "Credential", "ScanResult", "CredResult",
    "Scanner", "CredTester", "ReplayScanner", "ReplayCredTester",
    "NmapScanner", "HydraCredTester", "LootBook", "ReconRefused",
    "recon_subnet", "try_credentials", "autorun",
]
