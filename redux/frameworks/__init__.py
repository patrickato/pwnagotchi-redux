"""redux.frameworks — ATT&CK / D3FEND / PTES awareness + purple Range mode.

The registry maps every redux offensive action to its MITRE ATT&CK technique(s),
PTES phase, the detectors that should catch it, and a D3FEND countermeasure
(best-fit). Range mode uses that mapping to grade an attack against your own
detectors — ATT&CK technique → D3FEND countermeasure, with honest gaps. This layer
references the detector suite by name and never edits it.
"""
from .registry import (
    TechniqueMap, REGISTRY, actions, get, summarize, attack_defend_pairs,
)
from .range import Verdict, RangeResult, run_exercise, range_report

__all__ = [
    "TechniqueMap", "REGISTRY", "actions", "get", "summarize", "attack_defend_pairs",
    "Verdict", "RangeResult", "run_exercise", "range_report",
]
