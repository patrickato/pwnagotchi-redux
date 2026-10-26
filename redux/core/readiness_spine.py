"""Readiness spine — one truth about what the box can actually do.

Three systems each hold a piece of "is this capability real?":
  - the capability manifest knows the SOFTWARE truth (implemented? deps present?),
  - the Doctor knows the LIVE truth (is the radio/SDR/BT actually attached now?),
  - benchpass knows the QUALIFIED truth (has this been proven on real hardware?).

Read separately they disagree — the manifest says "software ready, attach a
radio", the Doctor says the radio is plugged in, benchpass says a gate passed.
The spine layers them into ONE readiness ladder so every surface (CLI, TFT, web,
the scenario checker) reads the same answer:

    PLANNED  <  TOOL_MISSING  <  NEEDS_HARDWARE  <  PRESENT  <  QUALIFIED

PRESENT = works in software and the hardware is detected, but no bench gate has
proven it. QUALIFIED = a benchpass gate attested it on real gear (or it's
software-only and there's nothing left to prove). Glass-box: each rung carries
the reason and which gate attested it. Pure/deterministic over injected probes.
"""
from __future__ import annotations

import shutil
from enum import IntEnum
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from . import manifest as M


class Rung(IntEnum):
    PLANNED = 0
    TOOL_MISSING = 1
    NEEDS_HARDWARE = 2
    PRESENT = 3            # software ready + hardware detected, not yet bench-proven
    QUALIFIED = 4          # attested on real hardware (or software-only: nothing to prove)

    @property
    def label(self) -> str:
        return {0: "planned", 1: "tool-missing", 2: "needs-hardware",
                3: "present", 4: "qualified"}[int(self)]


# Which benchpass gate id(s) attest each manifest capability on real hardware.
# A capability whose mapped gate PASSED is promoted to QUALIFIED. A hardware
# capability with no attesting gate can reach at most PRESENT (honest: we can
# detect it but have not proven it).
ATTESTS: Dict[str, Tuple[str, ...]] = {
    "wifi.capture": ("1.4", "1.6", "2b.2"),
    "wifi.pmkid": ("2b.2", "1.4"),
    "wifi.dna": ("2b.6",),
    "rf.dvr": ("2b.12", "1.2"),
    "net.recon": ("2b.8",),
    "gps.position": ("2.2",),
    "sense.csi": ("2b.1",),
    "mesh.swarm": ("2b.4",),
    "blue.detect": ("2b.12", "2b.7"),
}
# tolerate a couple of shorthand ids callers might pass
_ALIASES = {"gps": "gps.position", "sense": "sense.csi", "mesh": "mesh.swarm"}


def _rung_from_manifest(state: "M.Readiness") -> Rung:
    return {
        M.Readiness.PLANNED: Rung.PLANNED,
        M.Readiness.TOOL_MISSING: Rung.TOOL_MISSING,
        M.Readiness.NEEDS_HARDWARE: Rung.NEEDS_HARDWARE,
        M.Readiness.READY: Rung.PRESENT,          # provisional; may promote to QUALIFIED
    }[state]


def attesting_gates(feature_id: str) -> Tuple[str, ...]:
    fid = _ALIASES.get(feature_id, feature_id)
    return ATTESTS.get(fid, ())


def spine_readiness(feature: "M.Feature", *,
                    which: Callable[[str], Optional[str]] = shutil.which,
                    has_import: Callable[[str], bool] = M._default_has_import,
                    hardware_present: Callable[["M.Feature"], bool] = lambda f: False,
                    passed_gates: FrozenSet[str] = frozenset()) -> dict:
    """Layer the three systems into one rung for a feature (glass-box)."""
    state, base_reason = M.readiness(feature, which=which, has_import=has_import,
                                     hardware_present=hardware_present)
    rung = _rung_from_manifest(state)
    gates = attesting_gates(feature.id)
    attested = tuple(g for g in gates if g in passed_gates)
    reason = base_reason

    if rung is Rung.PRESENT:
        if not feature.needs_hardware:
            # software-only and ready → nothing on hardware to attest
            rung, reason = Rung.QUALIFIED, "software-only — ready, nothing to bench-qualify"
        elif attested:
            rung = Rung.QUALIFIED
            reason = f"bench-qualified on real hardware (gate{'s' if len(attested) > 1 else ''} {', '.join(attested)})"
        elif gates:
            reason = f"present + detected; not yet bench-qualified (run gate {gates[0]})"
        else:
            reason = "present + detected; no bench gate maps to this capability"
    return {"feature": feature.id, "title": feature.title, "category": feature.category,
            "rung": rung.label, "rung_value": int(rung), "reason": reason,
            "attesting_gates": list(gates), "attested_by": list(attested)}


def passed_gates_from_ledger(ledger) -> FrozenSet[str]:
    """The set of benchpass gate ids that PASSED in a Ledger (fail-safe → empty)."""
    try:
        return frozenset(gid for gid, rec in getattr(ledger, "gates", {}).items()
                         if (rec or {}).get("status") == "pass")
    except Exception:
        return frozenset()


def spine_completion(features: Optional[List["M.Feature"]] = None, *,
                     passed_gates: FrozenSet[str] = frozenset(),
                     which: Callable[[str], Optional[str]] = shutil.which,
                     has_import: Callable[[str], bool] = M._default_has_import,
                     hardware_present: Callable[["M.Feature"], bool] = lambda f: False) -> dict:
    """The unified completion view — one readiness truth across all capabilities."""
    feats = features if features is not None else M.list_features()
    rows = [spine_readiness(f, which=which, has_import=has_import,
                            hardware_present=hardware_present, passed_gates=passed_gates)
            for f in feats]
    by_rung: Dict[str, int] = {}
    for r in rows:
        by_rung[r["rung"]] = by_rung.get(r["rung"], 0) + 1
    rows.sort(key=lambda r: (-r["rung_value"], r["feature"]))
    q = by_rung.get("qualified", 0)
    return {
        "total": len(rows), "by_rung": by_rung, "rows": rows,
        "qualified": q, "present": by_rung.get("present", 0),
        "summary": (f"{q} qualified, {by_rung.get('present', 0)} present (detected, "
                    f"not bench-proven), {by_rung.get('needs-hardware', 0)} need hardware, "
                    f"{by_rung.get('tool-missing', 0)} need a tool, "
                    f"{by_rung.get('planned', 0)} planned"),
    }
