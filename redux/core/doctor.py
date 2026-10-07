"""Doctor — headless self-diagnosis that makes the capability graph legible.

The Doctor is a READ-ONLY layer over canonical state (the capability graph, the
Governor's decision, the Scope, detector/sighting counts). It doesn't poll a
second telemetry system and it never *acts* — a finding is a diagnosis, not an
executed change. It turns "4 disconnected warnings" into plain-English findings
with a cause and, where relevant, the blast-radius ("losing this affects N
things, M have a fallback").

Two honesty rules carried from the sibling platform:
  - **running != working**: a finding reflects whether a capability is actually
    satisfied, not whether a module imported.
  - **a green page never implies it checked what it couldn't**: `coverage()`
    reports which areas the Doctor had no basis to assess (UNKNOWN), so OK means
    "checked and fine," not "didn't look."

It is headless by design: the TFT, the web dashboard, and a phone are all just
views of the one Doctor report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from .capabilities import Cap, CapabilityGraph


class Status(str, Enum):
    OK = "ok"
    ATTENTION = "attention"        # worth knowing, not broken
    DEGRADED = "degraded"          # working in a reduced way
    ACTION_REQUIRED = "action"     # needs a decision/fix
    UNKNOWN = "unknown"            # the Doctor had no basis to assess this


_RANK = {Status.OK: 0, Status.ATTENTION: 1, Status.DEGRADED: 2, Status.ACTION_REQUIRED: 3}


@dataclass(frozen=True)
class Finding:
    area: str
    status: Status
    summary: str                         # plain English, no Linux jargon
    reason: str = ""                     # the causal "why"
    detail: Dict = field(default_factory=dict)
    remediation: str = ""                # what the operator could do (never auto-done)


@dataclass(frozen=True)
class DoctorInputs:
    """A read-only snapshot the probes reason over. Any field left None means the
    Doctor can't assess that area → it's reported as coverage gap, not a pass."""
    graph: Optional[CapabilityGraph] = None
    governor: Optional[object] = None     # a GovDecision (mode / reason / interval_scale)
    scope: Optional[object] = None        # a Scope (summary())
    detector_count: Optional[int] = None
    sightings: Optional[int] = None


# --- built-in probes --------------------------------------------------------- #

def probe_thermal_power(inp: DoctorInputs) -> Finding:
    g = inp.governor
    if g is None:
        return Finding("thermal/power", Status.UNKNOWN,
                       "No thermal/power readings yet.",
                       "needs a hardware collector feeding the Governor")
    mode = getattr(getattr(g, "mode", None), "value", "full")
    reason = getattr(g, "reason", "")
    st = {"full": Status.OK, "guarded": Status.ATTENTION,
          "reduced": Status.DEGRADED, "survival": Status.ACTION_REQUIRED}.get(mode, Status.UNKNOWN)
    summ = {"full": "Running at full performance.",
            "guarded": "Easing off slightly under load.",
            "reduced": "Throttled to manage heat/power.",
            "survival": "Shedding load hard — hot or low battery."}.get(mode, "Unknown resource state.")
    rem = "Improve airflow or move to power; the Governor recovers on its own once it cools/charges." \
        if st in (Status.DEGRADED, Status.ACTION_REQUIRED) else ""
    return Finding("thermal/power", st, summ, reason, {"mode": mode}, rem)


def _cap_probe(inp: DoctorInputs, cap: Cap, area: str, *, needed: bool) -> Finding:
    if inp.graph is None:
        return Finding(area, Status.UNKNOWN, f"Can't assess {area}.", "no capability graph provided")
    ex = inp.graph.explain(cap)
    if ex["available"]:
        return Finding(area, Status.OK, f"{area}: {ex['active_provider']} is providing it.",
                       ex["reason"], {"provider": ex["active_provider"]})
    br = inp.graph.blast_radius(cap)
    st = Status.ACTION_REQUIRED if needed else Status.ATTENTION
    rem = "Plug in / enable a provider for this capability." if needed else ""
    return Finding(area, st, f"{area} is unavailable.", br["reason"],
                   {"candidates": ex["candidates"], "affected": br["affected"]}, rem)


def probe_capture_radio(inp: DoctorInputs) -> Finding:
    return _cap_probe(inp, Cap.RADIO_WIFI_MONITOR, "capture radio", needed=True)


def probe_location(inp: DoctorInputs) -> Finding:
    # location is useful but not required for most ops → attention, not action
    return _cap_probe(inp, Cap.LOCATION_POSITION, "location", needed=False)


def probe_detectors(inp: DoctorInputs) -> Finding:
    if inp.detector_count is None:
        return Finding("detectors", Status.UNKNOWN, "Can't assess detectors.", "no detector count provided")
    if inp.detector_count > 0:
        return Finding("detectors", Status.OK, f"{inp.detector_count} detectors armed and watching.",
                       "the detect engine is populated")
    return Finding("detectors", Status.DEGRADED, "No detectors are running.",
                   "the detect engine is empty", remediation="check the detector registry")


def probe_scope(inp: DoctorInputs) -> Finding:
    if inp.scope is None:
        return Finding("scope", Status.UNKNOWN, "Can't assess the authorized-target scope.",
                       "no scope provided")
    s = inp.scope.summary()
    if s["empty"]:
        return Finding("scope", Status.ATTENTION, "No targets armed.",
                       "the authorized-target scope is empty — offensive functions will refuse until you arm one",
                       {"jobs": s["jobs"]}, "redux scope add / import / arm-lab")
    return Finding("scope", Status.OK,
                   f"{s['active']} authorized target(s) armed" + (f" across jobs {', '.join(s['jobs'])}" if s["jobs"] else "") + ".",
                   "offensive functions are aimed at your declared scope",
                   {"active": s["active"], "jobs": s["jobs"]})


def probe_capture_engine(inp: DoctorInputs) -> Finding:
    """Which capture engine is actually providing CAPTURE_HANDSHAKE — so a box with
    no engine present (no AngryOxide binary, no bettercap driver) can't read clean.
    Not in the built-in set (needs a graph that registers capture providers); the
    Augur doctor path and boot-POST add it, where that graph exists."""
    if inp.graph is None:
        return Finding("capture engine", Status.UNKNOWN, "Can't assess the capture engine.",
                       "no capability graph provided")
    ex = inp.graph.explain(Cap.CAPTURE_HANDSHAKE)
    if not ex.get("candidates"):
        return Finding("capture engine", Status.UNKNOWN, "Can't assess the capture engine.",
                       "no capture provider registered in the graph")
    if ex["available"]:
        return Finding("capture engine", Status.OK,
                       f"capture engine: {ex['active_provider']} is providing it.", ex["reason"],
                       {"provider": ex["active_provider"]})
    return Finding("capture engine", Status.ACTION_REQUIRED, "No capture engine is available.",
                   ex["reason"], {"candidates": ex["candidates"]},
                   "install AngryOxide or bring up the bettercap driver")


BUILTIN_PROBES: List[Callable[[DoctorInputs], Finding]] = [
    probe_thermal_power, probe_capture_radio, probe_location, probe_detectors, probe_scope,
]


@dataclass
class Doctor:
    probes: List[Callable[[DoctorInputs], Finding]] = field(default_factory=lambda: list(BUILTIN_PROBES))

    def run(self, inp: DoctorInputs) -> List[Finding]:
        return [p(inp) for p in self.probes]

    def report(self, inp: DoctorInputs) -> dict:
        findings = self.run(inp)
        known = [f for f in findings if f.status is not Status.UNKNOWN]
        overall = max((f.status for f in known), key=lambda s: _RANK[s]) if known else Status.UNKNOWN
        # field summary: OK / ATTENTION / DEGRADED / ACTION REQUIRED, one glance
        label = {Status.OK: "OK", Status.ATTENTION: "ATTENTION", Status.DEGRADED: "DEGRADED",
                 Status.ACTION_REQUIRED: "ACTION REQUIRED", Status.UNKNOWN: "UNKNOWN"}[overall]
        return {
            "overall": overall.value,
            "label": label,
            "findings": [
                {"area": f.area, "status": f.status.value, "summary": f.summary,
                 "reason": f.reason, "remediation": f.remediation, "detail": f.detail}
                for f in findings
            ],
            "coverage": self.coverage(findings),
        }

    def coverage(self, findings: List[Finding]) -> dict:
        """What the Doctor could and could not assess — so a clean report never
        implies it checked something it had no basis for."""
        assessed = [f.area for f in findings if f.status is not Status.UNKNOWN]
        gaps = [f.area for f in findings if f.status is Status.UNKNOWN]
        return {"assessed": assessed, "not_assessed": gaps,
                "reason": (f"{len(gaps)} area(s) could not be assessed: {', '.join(gaps)}"
                           if gaps else "all probed areas were assessed")}
