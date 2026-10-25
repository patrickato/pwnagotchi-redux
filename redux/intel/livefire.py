"""Live-fire purple range — attack your own gear, score what blue caught.

`range demo` scores a *simulated* attack set. This scores a REAL one: the red
side (the Operator) fires at your own armed gear, the blue side (Sentinel /
your detectors) reports what it saw, and every technique gets a verdict —
DETECTED / PARTIAL / MISSED — so you see your own attacks from the defender's
chair. Red and blue in one box, which is what the cage is for.

LAB-only: a POWER capability (`range.livefire`), gated by authorize_power, so
it is hidden and refused in FIELD and needs explicit confirmation in LAB —
because it transmits real attacks (at your own gear).

Scoring reuses redux.frameworks.range (run_exercise / range_report) rather than
reimplementing it. Pure: the executed attack actions and the fired-detector sets
are passed in, so the scorecard is testable; the live run wires the Operator and
the Sentinel into those inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.mode import LAB, FIELD


@dataclass(frozen=True)
class LiveFireReport:
    ran: bool
    mode: str
    reason: str
    results: Tuple[dict, ...] = ()        # per-technique RangeResult dicts
    coverage: Optional[dict] = None       # range_report() summary (counts, gaps)

    @property
    def caught(self) -> int:
        return sum(1 for r in self.results if r.get("verdict") == "detected")

    @property
    def missed(self) -> int:
        return sum(1 for r in self.results if r.get("verdict") == "missed")


def live_fire(scope, *, executed_actions: Sequence[str],
              fired_by_action: Optional[Dict[str, Sequence[str]]] = None,
              confirmed: bool = False) -> LiveFireReport:
    """Score a real attack run. `executed_actions` are the techniques red actually
    fired (e.g. the Operator's executed steps); `fired_by_action` maps each to the
    detectors blue reported (from the Sentinel). LAB-gated, fail-closed."""
    from ..core.power_tier import authorize_power
    from ..frameworks.range import run_exercise, range_report
    mode = LAB if getattr(scope, "lab_mode", False) else FIELD
    ok, why = authorize_power("range.livefire",
                              lab_mode=getattr(scope, "lab_mode", False), confirmed=confirmed)
    if not ok:
        return LiveFireReport(ran=False, mode=mode, reason=why)

    fired_by_action = fired_by_action or {}
    results = [run_exercise(a, list(fired_by_action.get(a, []))) for a in executed_actions]
    coverage = range_report(results)
    report_dicts = tuple(r.to_dict() for r in results)
    caught = sum(1 for r in report_dicts if r.get("verdict") == "detected")
    total = len(report_dicts)
    return LiveFireReport(ran=True, mode=mode, results=report_dicts, coverage=coverage,
                          reason=f"live-fire: {total} technique(s) fired, {caught} caught by blue")
