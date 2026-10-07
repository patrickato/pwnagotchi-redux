"""Autonomous glass-box kill-chain operator — the conductor.

Everyone bolting an LLM onto autonomous offense lets a model improvise about who to
hit. redux does the opposite: a **deterministic** operator that sequences the chain
— recon → capture → crack → network-pivot (scan → cred-test → loot) — where every
step is gated and carries a human-readable reason, and no firing step runs unless
all three gates pass:

  - **Posture** — offense must be enabled (a detection-only persona runs recon only).
  - **Scope** — the target must be authorized in the central Scope (aiming).
  - **Capability** — the capability the step needs must actually be present
    (e.g. a capture engine for the capture step) — no pretending.

`plan()` is a dry run: it shows what it WOULD do and, where a gate fails, exactly
why that branch is blocked — nothing executes. `run()` executes allowed steps via
**injected executors** (so it is testable with no radio and no tools), advancing a
target's chain only while the previous step actually succeeded, and records every
step as an action that feeds the engagement report. Honest: a blocked or failed
step stops that target's chain and says why; nothing is invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..core.scope import classify_target


class Phase(str, Enum):
    RECON = "recon"
    CAPTURE = "capture"
    CRACK = "crack"
    PIVOT_SCAN = "pivot_scan"
    PIVOT_CRED = "pivot_cred"
    LOOT = "loot"


# the offensive chain per target (recon precedes the whole run)
CHAIN: Tuple[Phase, ...] = (Phase.CAPTURE, Phase.CRACK, Phase.PIVOT_SCAN,
                            Phase.PIVOT_CRED, Phase.LOOT)

# phase -> (frameworks/report action key, capability id needed or None)
_PHASE_META: Dict[Phase, Tuple[str, Optional[str]]] = {
    Phase.CAPTURE: ("handshake_capture", "capture.handshake"),
    Phase.CRACK: ("offline_crack", None),
    Phase.PIVOT_SCAN: ("net_scan", None),
    Phase.PIVOT_CRED: ("cred_test", None),
    Phase.LOOT: ("loot", None),
}


@dataclass(frozen=True)
class Step:
    target: str
    phase: Phase
    action: str
    allowed: bool
    authorized: bool
    reason: str
    result: str = ""
    executed: bool = False

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["phase"] = self.phase.value
        return d


@dataclass
class Operator:
    """Plans and runs the kill-chain over the live Scope, posture and capabilities."""
    scope: object
    offense_enabled: bool = True
    caps: Dict[str, bool] = field(default_factory=dict)   # capability id -> present

    # --- gates ------------------------------------------------------------- #

    def _authorized(self, target: str) -> Tuple[bool, str]:
        if self.scope is None:
            return False, "no scope — target not authorized"
        kind = classify_target(target)
        if kind == "bssid":
            return self.scope.authorize(bssid=target)
        if kind == "ssid":
            return self.scope.authorize(ssid=target)
        import ipaddress
        try:
            ip = str(ipaddress.ip_network(target, strict=False).network_address)
        except ValueError:
            ip = target
        return self.scope.authorize(ip=ip)

    def _gate(self, target: str, phase: Phase) -> Tuple[bool, bool, str]:
        """(allowed, authorized, reason) for one firing step."""
        if not self.offense_enabled:
            return False, False, "posture is detection-only — offensive chain not run"
        authorized, areason = self._authorized(target)
        if not authorized:
            return False, False, f"not authorized: {areason}"
        cap = _PHASE_META[phase][1]
        if cap is not None and not self.caps.get(cap, False):
            return False, True, f"capability '{cap}' not present (e.g. no capture engine)"
        return True, True, f"authorized and ready — {areason}"

    # --- plan (dry run) ---------------------------------------------------- #

    def plan(self, targets: Sequence[str]) -> List[Step]:
        """What the operator WOULD do, with a reason per step. Nothing executes.
        A target blocked at a firing step stops there (you can't crack what you
        couldn't capture); downstream steps are shown as contingent."""
        steps: List[Step] = [Step("(all)", Phase.RECON, "wifi_recon", True, True,
                                   "passive discovery — always allowed")]
        for t in targets:
            blocked = False
            for phase in CHAIN:
                action = _PHASE_META[phase][0]
                if blocked:
                    steps.append(Step(t, phase, action, False, False,
                                      "contingent — a prior step was blocked/failed"))
                    continue
                allowed, authorized, reason = self._gate(t, phase)
                steps.append(Step(t, phase, action, allowed, authorized, reason))
                if not allowed:
                    blocked = True
                elif phase != Phase.CAPTURE:
                    # downstream of capture is also contingent on the prior step
                    # actually succeeding at run time; in a dry plan we mark that.
                    pass
        return steps

    # --- run (executes injected executors) --------------------------------- #

    def run(self, targets: Sequence[str],
            executors: Dict[Phase, Callable[[str], Tuple[bool, str]]],
            *, recon: Optional[Callable[[], Tuple[bool, str]]] = None) -> dict:
        """Execute the chain. `executors[phase](target) -> (ok, result)`; a missing
        executor for a needed phase stops that target honestly. Advances only while
        the previous step succeeded. Returns steps + an action log for the report."""
        steps: List[Step] = []
        # recon first (passive)
        rok, rres = (recon() if recon else (True, "recon skipped (no executor)"))
        steps.append(Step("(all)", Phase.RECON, "wifi_recon", True, True,
                          "passive discovery", result=rres, executed=recon is not None))
        for t in targets:
            for phase in CHAIN:
                action = _PHASE_META[phase][0]
                allowed, authorized, reason = self._gate(t, phase)
                if not allowed:
                    steps.append(Step(t, phase, action, False, authorized, reason))
                    break      # gate closed → stop this target's chain
                ex = executors.get(phase)
                if ex is None:
                    steps.append(Step(t, phase, action, True, authorized,
                                      reason + f"; no executor for {phase.value} — stopping",
                                      executed=False))
                    break
                ok, res = ex(t)
                steps.append(Step(t, phase, action, True, authorized, reason,
                                  result=res, executed=True))
                if not ok:
                    break      # step failed → no point continuing this target
        return {
            "steps": [s.to_dict() for s in steps],
            "log": self._as_actions(steps),
            "summary": self._summary(steps, targets),
        }

    # --- helpers ----------------------------------------------------------- #

    @staticmethod
    def _as_actions(steps: Sequence[Step]) -> list:
        """The executed, *targeted* steps as EngagementAction-shaped objects for the
        report. Passive recon (target '(all)') is excluded — it isn't aimed at a
        scoped target, so authorization-checking it would be a false flag."""
        from ..report import EngagementAction
        out = []
        ts = 0.0
        for s in steps:
            if not s.executed or s.phase is Phase.RECON:
                continue
            out.append(EngagementAction(ts=ts, action=s.action, target=s.target,
                                        reason=s.reason, result=s.result))
            ts += 1.0
        return out

    @staticmethod
    def _summary(steps: Sequence[Step], targets: Sequence[str]) -> dict:
        executed = [s for s in steps if s.executed]
        blocked = [s for s in steps if not s.allowed]
        return {
            "targets": len(targets),
            "executed_steps": len(executed),
            "blocked_steps": len(blocked),
            "reason": (f"{len(executed)} step(s) executed, {len(blocked)} blocked by a gate"
                       if executed or blocked else "nothing to do"),
        }
