"""Purple Range mode — attack yourself, grade your own detectors.

The purple-team move no single box ships: run a Scope-aimed attack, observe which
of your *own* detectors fired, and score the coverage as ATT&CK technique →
D3FEND countermeasure, naming the gaps. One device, both sides of the engagement.

The value is in the honest MISS: if an attack ran and no expected detector fired,
that's a real coverage gap, and Range mode says so plainly rather than papering
over it. NOT_APPLICABLE is distinct from MISSED — a passive/network-layer
technique that the wireless suite isn't meant to catch is not a failure.

Pure: `run_exercise(action, fired_detectors)` takes the detectors observed during
the attack (in the field, the detect engine's alerts; in a test/demo, a given
set) and returns a graded result. No radio, no detectors edited.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Sequence

from .registry import REGISTRY, TechniqueMap, get


class Verdict(str, Enum):
    DETECTED = "detected"           # every expected detector fired
    PARTIAL = "partial"             # some fired
    MISSED = "missed"               # attack ran, nothing expected fired — a real gap
    NOT_APPLICABLE = "n/a"          # nothing in the wireless suite should catch this


def _norm(names: Sequence[str]) -> set:
    return {(n or "").strip().lower() for n in names if n}


@dataclass(frozen=True)
class RangeResult:
    action: str
    label: str
    attack_ids: List[str]
    ptes_phase: str
    expected: List[str]
    fired: List[str]
    matched: List[str]
    verdict: Verdict
    defend: List[str]
    reason: str

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["verdict"] = self.verdict.value
        return d


def run_exercise(action: str, fired_detectors: Sequence[str]) -> RangeResult:
    m: TechniqueMap = get(action)
    expected = _norm(m.detects)
    fired = _norm(fired_detectors)
    matched = expected & fired

    if not expected:
        verdict = Verdict.NOT_APPLICABLE
        reason = f"{m.label}: no wireless detector is expected to catch this ({m.note or 'by design'})"
    elif matched == expected:
        verdict = Verdict.DETECTED
        reason = f"{m.label}: all expected detectors fired ({', '.join(sorted(matched))})"
    elif matched:
        verdict = Verdict.PARTIAL
        missed = expected - matched
        reason = (f"{m.label}: partial — fired {', '.join(sorted(matched))}; "
                  f"MISSED {', '.join(sorted(missed))}")
    else:
        verdict = Verdict.MISSED
        reason = (f"{m.label}: GAP — attack ran, none of the expected detectors fired "
                  f"({', '.join(sorted(expected))})")

    return RangeResult(
        action=m.action, label=m.label, attack_ids=list(m.attack_ids),
        ptes_phase=m.ptes_phase, expected=sorted(expected), fired=sorted(fired),
        matched=sorted(matched), verdict=verdict, defend=list(m.defend), reason=reason)


def range_report(results: Sequence[RangeResult]) -> dict:
    """Aggregate coverage across an exercise set. Coverage is measured only over
    the APPLICABLE techniques (NOT_APPLICABLE ones don't count for or against)."""
    counts = {v.value: 0 for v in Verdict}
    for r in results:
        counts[r.verdict.value] += 1
    applicable = [r for r in results if r.verdict is not Verdict.NOT_APPLICABLE]
    detected = sum(1 for r in applicable if r.verdict is Verdict.DETECTED)
    coverage = (detected / len(applicable)) if applicable else None
    gaps = [{"action": r.action, "attack": r.attack_ids, "expected": r.expected}
            for r in results if r.verdict is Verdict.MISSED]
    return {
        "exercises": len(results),
        "counts": counts,
        "applicable": len(applicable),
        "fully_detected": detected,
        "coverage": None if coverage is None else round(coverage, 3),
        "gaps": gaps,
        "reason": (f"{detected}/{len(applicable)} applicable techniques fully detected"
                   if applicable else "no applicable techniques in this set"),
    }
