"""Network pivot — turn a recovered key into a foothold, scope-gated by mode.

The crack->pivot bridge: once a PSK (or service credential) is in the vault,
feed it into the network-layer kill-chain — recon the subnet, then spray the
recovered/supplied credentials at the services found (credential reuse is how
real compromises escalate). Every recovered credential is persisted to the
vault and logged.

Mode gating is the whole point here (this is a firing path):

  * FIELD (shipped default) — conservative. The pivot PLANS but does not fire
    unless `authorized=True`, and even then every subnet/host must be in the
    central Scope (recon_subnet/try_credentials enforce it). Out-of-scope ->
    refused, recorded as a note, never run.
  * LAB — unlocked. Private subnets are approved on sight (scope.lab_mode), the
    chain runs end-to-end with no per-step authorization needed.

Public ranges are never auto-approved, even in LAB (the one guard that stays).
Execution reuses netrecon's already-gated primitives; this layer adds the
plan-not-fire posture and the vault/authlog persistence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..core.mode import LAB, FIELD
from ..netrecon.pipeline import recon_subnet, try_credentials, ReconRefused
from .vault import LootEntry, LootVault

_DEFAULT_SERVICES = ("ssh", "ftp", "smb", "rdp", "telnet", "vnc", "http", "sql")


@dataclass(frozen=True)
class PivotResult:
    executed: bool
    mode: str                       # "LAB" | "FIELD"
    reason: str
    planned_steps: Tuple[str, ...]
    hosts: int = 0
    creds_found: int = 0
    vault_added: int = 0
    notes: Tuple[str, ...] = ()


def pivot(scope, *, authorized: bool = False, subnet: Optional[str] = None,
          usernames: Sequence[str] = (), passwords: Sequence[str] = (),
          services: Sequence[str] = _DEFAULT_SERVICES,
          scanner=None, tester=None, vault: Optional[LootVault] = None,
          logger=None, now: Optional[float] = None) -> PivotResult:
    """Recon + credential-reuse over the chosen subnet(s). FIELD plans unless
    authorized; LAB runs. Scope is enforced by the netrecon primitives either way."""
    now = time.time() if now is None else now
    mode = LAB if getattr(scope, "lab_mode", False) else FIELD
    cidrs = [subnet] if subnet else [e.value for e in scope.active_entries() if e.kind == "cidr"]

    steps: List[str] = [f"recon {c}" for c in cidrs]
    if usernames and passwords:
        steps.append(f"credential-reuse: spray {len(usernames)}×{len(passwords)} at "
                     f"{', '.join(services)} services on discovered hosts")

    if not cidrs:
        return PivotResult(False, mode, "no subnet to pivot into — pass --subnet or arm a CIDR in scope",
                           tuple(steps))
    if mode == FIELD and not authorized:
        return PivotResult(False, mode,
                           "FIELD mode: plan only — re-run with --authorized to fire; every target "
                           "must be armed in the scope", tuple(steps))

    notes: List[str] = []
    hosts = found = added = 0
    for cidr in cidrs:
        try:
            res = recon_subnet(cidr, scope=scope, scanner=scanner)
        except ReconRefused as e:
            notes.append(str(e))
            continue
        if not res.available:
            notes.append(f"{cidr}: {res.reason}")
            continue
        hosts += len(res.hosts)
        notes.append(f"{cidr}: {res.reason}")
        if not (usernames and passwords):
            continue
        for h in res.hosts:
            for p in h.ports:
                if services and p.service and p.service.lower() not in services:
                    continue
                try:
                    cr = try_credentials(h.ip, p.number, p.service or p.proto,
                                         usernames, passwords, scope=scope, tester=tester)
                except ReconRefused as e:
                    notes.append(str(e))
                    continue
                if cr.found:
                    found += 1
                    if vault is not None:
                        c = cr.found
                        newly = vault.add(LootEntry(kind="service-cred", target=c.ip, secret=c.secret,
                                                    service=c.service, username=c.username,
                                                    source="pivot", ts=now), logger=logger)
                        added += int(newly)
    if logger:
        logger("pivot.run", {"mode": mode, "cidrs": cidrs, "hosts": hosts, "creds": found})
    return PivotResult(True, mode, f"{hosts} host(s), {found} credential(s) recovered",
                       tuple(steps), hosts, found, added, tuple(notes))
