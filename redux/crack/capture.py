"""Capture providers — bettercap and AngryOxide behind one capability.

The engine bake-off (docs/ENGINE_BAKEOFF.md) concluded: run both. bettercap is the
always-on nervous system; AngryOxide is the capture *scalpel* for surgical,
validated-crackable handshakes. This module expresses both as `CAPTURE_HANDSHAKE`
providers so the capability graph picks the best available one and explains why,
with honest tool-absence (AngryOxide present only if its binary is on PATH).

Two invariants that make this safe and legible:

  - **Scope decides WHERE, always.** AngryOxide's own default is "attack every AP
    in range" — we never allow that. redux passes *only* armed targets as `-t`
    and **refuses to run with an empty scope** rather than sweeping broadly. That
    is the aiming model: the engine aims harder, Scope still says where.
  - **Posture maps to transmit.** When offense isn't enabled (a detection-only
    persona, or no armed targets), the plan runs `--notransmit` — passive capture
    only, no attack frames. Active posture + armed scope = the full scalpel.

Pure/testable: `available()` takes an injected `which`, and `run()` an injected
runner, so command construction is verified with no binary and no radio.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol

DEFAULT_CAPTURE_DIR = "/etc/pwnagotchi/handshakes"


def _armed_wifi_targets(scope) -> List[str]:
    """BSSID/SSID entries from the central Scope (CIDRs aren't Wi-Fi capture
    targets). Empty when nothing is armed."""
    if scope is None:
        return []
    out: List[str] = []
    for e in scope.active_entries():
        if getattr(e, "kind", None) in ("bssid", "ssid"):
            out.append(e.value)
    return out


@dataclass(frozen=True)
class CapturePlan:
    engine: str
    runnable: bool
    passive: bool
    targets: List[str]
    argv: List[str]
    outputs: dict
    reason: str

    def to_dict(self) -> dict:
        return {"engine": self.engine, "runnable": self.runnable, "passive": self.passive,
                "targets": list(self.targets), "argv": list(self.argv),
                "outputs": dict(self.outputs), "reason": self.reason}


class CaptureProvider(Protocol):
    name: str
    def available(self) -> bool: ...
    def plan(self, scope, *, active: bool = True, iface: str = "") -> CapturePlan: ...


@dataclass
class AngryOxideConfig:
    binary: str = "angryoxide"
    iface: str = "wlan1"
    out_dir: str = DEFAULT_CAPTURE_DIR
    band: Optional[int] = None          # 2 / 5 / 6 ; ignored if channels set
    channels: tuple = ()
    dwell: float = 2.0
    rate: int = 2
    autohunt: bool = False
    autoexit: bool = True


@dataclass
class AngryOxideProvider:
    """The scalpel. Builds a headless, Scope-aimed AngryOxide invocation."""
    config: AngryOxideConfig = field(default_factory=AngryOxideConfig)
    which: Callable[[str], Optional[str]] = shutil.which
    name: str = "angryoxide"

    def available(self) -> bool:
        return self.which(self.config.binary) is not None

    def plan(self, scope, *, active: bool = True, iface: str = "") -> CapturePlan:
        iface = iface or self.config.iface
        targets = _armed_wifi_targets(scope)
        passive = not active
        if not targets and active:
            return CapturePlan(
                "angryoxide", runnable=False, passive=False, targets=[], argv=[], outputs={},
                reason=("scope has no armed Wi-Fi targets — AngryOxide will NOT run a broad/unaimed "
                        "attack; arm a BSSID/SSID first (redux scope add / arm-lab)"))
        argv = [self.config.binary, "-i", iface, "--headless", "--notar", "-o", self.config.out_dir]
        if self.config.autoexit and targets:
            argv.append("--autoexit")
        if self.config.autohunt and not passive:
            argv.append("--autohunt")
        if self.config.channels:
            argv += ["-c", ",".join(str(c) for c in self.config.channels)]
        elif self.config.band:
            argv += ["-b", str(self.config.band)]
        argv += ["--dwell", str(self.config.dwell), "-r", str(self.config.rate)]
        for t in targets:
            argv += ["-t", t]
        if passive:
            argv.append("--notransmit")      # posture: no attack frames
        outputs = {"dir": self.config.out_dir,
                   "hashline": f"{self.config.out_dir}/*.hc22000 (→ crack pipeline)",
                   "pcapng": f"{self.config.out_dir}/*.pcapng (Kismet fmt → geo/Dex)"}
        reason = (f"{'passive capture' if passive else 'surgical capture+attack'} on "
                  f"{len(targets)} armed target(s) via AngryOxide — "
                  f"validated 22000 to the crack pipeline, pcapng to the sighting store")
        return CapturePlan("angryoxide", True, passive, targets, argv, outputs, reason)

    def run(self, scope, *, active: bool = True, iface: str = "",
            runner: Optional[Callable[[List[str]], object]] = None) -> dict:
        """Spawn AngryOxide (real on-device). Honest: if the binary isn't present
        it does NOT run and says so (the graph then falls back to bettercap)."""
        if not self.available():
            return {"ran": False, "available": False, "engine": "angryoxide",
                    "reason": f"angryoxide not installed ('{self.config.binary}' not on PATH)"}
        plan = self.plan(scope, active=active, iface=iface)
        if not plan.runnable:
            return {"ran": False, "available": True, "engine": "angryoxide",
                    "argv": [], "reason": plan.reason}
        run = runner or (lambda argv: subprocess.run(argv))   # real spawn on device
        run(plan.argv)
        return {"ran": True, "available": True, "engine": "angryoxide", "argv": plan.argv,
                "targets": plan.targets, "passive": plan.passive, "outputs": plan.outputs,
                "reason": plan.reason}


@dataclass
class BettercapProvider:
    """The nervous system's capture path — always available when the driver is up,
    and able to run passive recon even with an empty scope (so it's the honest
    fallback). PMKID-first / deauth only against armed targets when active."""
    driver: object = None
    name: str = "bettercap"

    def available(self) -> bool:
        return self.driver is not None

    def plan(self, scope, *, active: bool = True, iface: str = "") -> CapturePlan:
        targets = _armed_wifi_targets(scope)
        passive = not active or not targets
        if targets and active:
            reason = f"bettercap recon + PMKID-first (assoc) on {len(targets)} armed target(s)"
        else:
            reason = "bettercap passive recon (no armed targets / passive posture)"
        return CapturePlan("bettercap", runnable=True, passive=passive, targets=targets,
                           argv=[], outputs={"pcap": "~/bettercap-wifi-handshakes.pcap"},
                           reason=reason)


def select_capture_provider(providers: List[CaptureProvider]):
    """First available provider in preference order → (provider, reason)."""
    for p in providers:
        if p.available():
            return p, f"{p.name} is present and selected"
    return None, "no capture engine available"


def register_capture_providers(graph, *, angryoxide_present: bool, bettercap_present: bool):
    """Register both engines as CAPTURE_HANDSHAKE providers so the capability graph
    can name the active engine and explain it. AngryOxide is registered first →
    preferred when present."""
    from ..core.capabilities import Cap, Provider
    graph.register(Provider.of(
        "angryoxide-capture", provides=[Cap.CAPTURE_HANDSHAKE], present=angryoxide_present,
        reason=("AngryOxide present — surgical, validated capture" if angryoxide_present
                else "AngryOxide binary not on PATH")))
    graph.register(Provider.of(
        "bettercap-capture", provides=[Cap.CAPTURE_HANDSHAKE], present=bettercap_present,
        reason=("bettercap driver up — general capture" if bettercap_present
                else "no bettercap driver")))
    return graph
