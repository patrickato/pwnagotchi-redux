"""Unleashed — the LAB-only full-chain autopilot.

In your closed cage (no bystanders, everything yours) the box can be turned
loose: loop the Operator over every in-scope target, cycle after cycle, auto-
cracking what it captures — recon → capture → crack → pivot → loot → report,
hands-off. This is the capstone the whole gated chain was built toward.

It is a POWER capability: `authorize_power("campaign.unleashed", ...)` gates it,
so it is HIDDEN and REFUSED in FIELD and needs explicit confirmation in LAB.
That gate is the first thing this does — fail-closed, before anything runs.

Pure orchestration with injected executors / target source / cracker, so the
loop and the gate are fully testable; the real RF executors come from the live
daemon. Every step still flows through the Operator's scope gate and the signed
auth log — unleashed changes the CADENCE (continuous, all targets), never the
authorization model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .operator import Operator, Phase
from ..core.mode import LAB, FIELD


@dataclass(frozen=True)
class AutopilotCycle:
    targets: int
    executed: int
    blocked: int
    cracked: int


@dataclass(frozen=True)
class AutopilotReport:
    ran: bool
    mode: str
    reason: str
    cycles: Tuple[AutopilotCycle, ...] = ()

    @property
    def total_executed(self) -> int:
        return sum(c.executed for c in self.cycles)

    @property
    def total_cracked(self) -> int:
        return sum(c.cracked for c in self.cycles)

    @property
    def total_blocked(self) -> int:
        return sum(c.blocked for c in self.cycles)


def autopilot(scope, *, target_source: Callable[[], Sequence[str]],
              executors: Dict, recon: Optional[Callable] = None,
              crack_fn: Optional[Callable[[Sequence[dict]], int]] = None,
              max_cycles: int = 1, confirmed: bool = False,
              logger=None) -> AutopilotReport:
    """Run the operator over `target_source()` for up to `max_cycles`. LAB-gated
    via power_tier (refused+hidden in FIELD; needs confirmation in LAB).

    `crack_fn(loot_credentials) -> count` auto-cracks what a cycle captured (the
    recovered creds / loot entries); injected so the loop is testable."""
    from ..core.power_tier import authorize_power
    mode = LAB if getattr(scope, "lab_mode", False) else FIELD
    ok, why = authorize_power("campaign.unleashed",
                              lab_mode=getattr(scope, "lab_mode", False), confirmed=confirmed)
    if not ok:
        return AutopilotReport(ran=False, mode=mode, reason=why)

    op = Operator(scope, offense_enabled=True, caps={"capture.handshake": True})
    cycles: List[AutopilotCycle] = []
    for _ in range(max(1, int(max_cycles))):
        targets = list(target_source() or [])
        if not targets:
            cycles.append(AutopilotCycle(0, 0, 0, 0))
            continue
        out = op.run(targets, executors, recon=recon, logger=logger)
        executed = out["summary"]["executed_steps"]
        blocked = out["summary"]["blocked_steps"]
        cracked = 0
        if crack_fn is not None:
            loot = [s for s in out["log"] if getattr(s, "action", "") in ("offline_crack", "loot")]
            try:
                cracked = int(crack_fn(loot) or 0)
            except Exception:
                cracked = 0
        cycles.append(AutopilotCycle(len(targets), executed, blocked, cracked))
        if logger:
            try:
                logger("campaign.unleashed.cycle",
                       {"targets": len(targets), "executed": executed, "blocked": blocked, "cracked": cracked})
            except Exception:
                pass

    total = sum(c.executed for c in cycles)
    return AutopilotReport(ran=True, mode=mode, cycles=tuple(cycles),
                           reason=f"unleashed: {len(cycles)} cycle(s), {total} step(s) executed")
