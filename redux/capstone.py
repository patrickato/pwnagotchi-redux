"""Capstone — the one hands-off pass (Phase-1 gate 1.6).

This is the flagship demo in a single call: declare an intent, let the Radio
Orchestrator arrange whatever radios are present into roles, repoint the capture
engine at the monitor radio, and run a bounded capture — no manual `airmon-ng`,
no `iw` juggling. Every step carries a human-readable reason (glass-box), and
nothing transmits unless posture + Scope say so.

`plan_capstone()` is pure and hardware-free: give it a radio inventory and a
Scope and it returns the exact ordered steps the pass would take and why. The
CLI (`redux demo`) wraps it with a sample inventory for a dry run, or with the
live `probe()` for the real on-device pass. The one thing this module never does
is touch hardware or transmit; that is the CLI's live path and the physical gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .radio.orchestrator import Radio, Role, Intent, decide
from .crack.capture import _armed_wifi_targets


@dataclass
class Step:
    """One ordered step of the pass. `ok` is whether the step succeeded; `reason`
    is the glass-box why. No step that could transmit is ever marked done here."""
    step: str
    ok: bool
    detail: str
    reason: str

    def as_dict(self) -> dict:
        return {"step": self.step, "ok": self.ok,
                "detail": self.detail, "reason": self.reason}


@dataclass
class Capstone:
    intent: str
    steps: List[Step] = field(default_factory=list)
    assignment: dict = field(default_factory=dict)      # iface -> role value
    reasons: dict = field(default_factory=dict)         # iface -> why
    warnings: list = field(default_factory=list)
    capture_iface: Optional[str] = None
    posture: str = "passive"                            # passive | active
    transmit: bool = False
    armed_targets: int = 0
    ok: bool = False
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "intent": self.intent,
            "steps": [s.as_dict() for s in self.steps],
            "assignment": self.assignment,
            "reasons": self.reasons,
            "warnings": list(self.warnings),
            "capture_iface": self.capture_iface,
            "posture": self.posture,
            "transmit": self.transmit,
            "armed_targets": self.armed_targets,
            "ok": self.ok,
            "summary": self.summary,
        }


def plan_capstone(radios, intent, scope, *, offense_enabled: bool = False,
                  seconds: int = 30, engine_available: bool = True) -> Capstone:
    """Pure, hardware-free plan of the hands-off pass.

    Steps, in order: inventory -> orchestrate (intent->roles) -> pick capture
    radio -> repoint the engine -> posture (transmit vs passive) -> capture plan.
    `offense_enabled` is the persona/posture gate; transmission also requires at
    least one armed Wi-Fi target in `scope`. Nothing here transmits.
    """
    intent = Intent(intent)
    cap = Capstone(intent=intent.value)
    radios = list(radios)

    # 1. inventory
    if not radios:
        cap.steps.append(Step("inventory", False, "no radios present",
                              "plug a Wi-Fi adapter (or enable the onboard radio) and re-run"))
        cap.summary = "no radios — nothing to arrange"
        return cap
    names = ", ".join(r.iface for r in radios)
    cap.steps.append(Step("inventory", True, f"{len(radios)} radio(s): {names}",
                          "enumerated present radios before assigning any role"))

    # 2. orchestrate: intent -> roles (this is the flagship — no manual monitor mode)
    assign = decide(radios, intent)
    cap.assignment = {iface: role.value for iface, role in assign.roles.items()}
    cap.reasons = dict(assign.reasons)
    cap.warnings = list(assign.warnings)
    cap.steps.append(Step("orchestrate", True,
                          "; ".join(f"{i}->{r.value}" for i, r in assign.roles.items()),
                          "Radio Orchestrator mapped intent to roles; no airmon-ng/iw by hand"))

    # 3. pick the capture radio
    cap_ifaces = [i for i, r in assign.roles.items() if r is Role.CAPTURE]
    if not cap_ifaces:
        cap.steps.append(Step("capture_radio", False, "no radio took the capture role",
                              assign.reasons.get(next(iter(assign.roles), ""), "")
                              or "intent assigned no monitor-capable radio to capture"))
        cap.summary = f"{intent.value}: no capture radio — cannot run the capture pass"
        return cap
    cap.capture_iface = cap_ifaces[0]
    cap.steps.append(Step("capture_radio", True, cap.capture_iface,
                          assign.reasons.get(cap.capture_iface, "selected monitor radio for capture")))

    # 4. repoint the engine at the capture radio
    cap.steps.append(Step("repoint", bool(engine_available),
                          f"bettercap -> {cap.capture_iface}" if engine_available
                          else "capture engine not present",
                          "point the capture engine at the monitor radio"
                          if engine_available
                          else "install bettercap (or the AngryOxide pack) for the capture engine"))

    # 5. posture: transmit with offense enabled AND (an armed target, OR LAB mode —
    #    your own cage, where Scope approves on sight so no pre-arm is needed).
    armed = _armed_wifi_targets(scope)
    cap.armed_targets = len(armed)
    lab = bool(getattr(scope, "lab_mode", False))
    intent_active = intent is Intent.HUNT
    active = intent_active and offense_enabled and (bool(armed) or lab)
    cap.posture = "active" if active else "passive"
    cap.transmit = active
    if not intent_active:
        reason = f"{intent.value} is a passive intent; capture is listen-only (--notransmit)"
    elif not offense_enabled:
        reason = "offense not enabled (detection-only posture); capture stays passive (--notransmit)"
    elif armed:
        reason = f"offense enabled and {len(armed)} target(s) armed in Scope; active capture permitted"
    elif lab:
        reason = "LAB mode — your own cage; active capture approved on sight (no pre-arm needed)"
    else:
        reason = "Scope has no armed Wi-Fi target; nothing to aim at, so capture stays passive (--notransmit)"
    cap.steps.append(Step("posture", True, cap.posture, reason))

    # 6. the bounded capture plan (planned only; execution is the live/on-Pi path)
    mode = "active capture" if active else "passive capture"
    cap.steps.append(Step("capture", bool(engine_available),
                          f"{mode} on {cap.capture_iface} for {seconds}s"
                          if engine_available else "blocked: no capture engine",
                          f"bounded {seconds}s {mode}; stops on handshake or timeout"
                          if engine_available
                          else "capture engine unavailable; the arrange step still proved out"))

    cap.ok = engine_available
    if cap.ok:
        cap.summary = (f"{intent.value}: arranged {len(radios)} radio(s), capture on "
                       f"{cap.capture_iface} ({cap.posture}), engine ready — hands-off pass is wired")
    else:
        cap.summary = (f"{intent.value}: radios arranged and capture radio chosen "
                       f"({cap.capture_iface}), but no capture engine present")
    return cap


def sample_inventory() -> list:
    """A representative two-radio field setup for the dry run: the Pi's onboard
    2.4GHz radio plus a plugged monitor/injection USB adapter (the 'Alfa')."""
    return [
        Radio(iface="wlan0", phy="phy0", bands=frozenset({"2.4", "5"}),
              monitor=False, inject=False, driver="brcmfmac", onboard=True),
        Radio(iface="wlan1", phy="phy1", bands=frozenset({"2.4", "5"}),
              monitor=True, inject=True, driver="mt76x2u", onboard=False, usb_gen=2),
    ]
