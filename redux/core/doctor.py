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
    telemetry: Optional[dict] = None


# --- built-in probes --------------------------------------------------------- #

def probe_thermal_power(inp: DoctorInputs) -> Finding:
    g = inp.governor
    if g is None:
        return Finding("thermal/power", Status.UNKNOWN,
                       "No thermal/power readings yet.",
                       "needs a hardware collector feeding the Governor")
    mode = getattr(getattr(g, "mode", None), "value", None)
    observed = getattr(g, "observed_axes", ())
    telemetry = inp.telemetry
    if telemetry is not None:
        from .governor import finite_number
        from .telemetry import FLAGS, MAX_AGE_SECONDS
        if (not isinstance(telemetry, dict)
                or not isinstance(telemetry.get("values"), dict)
                or telemetry.get("flags") is not None and not isinstance(telemetry.get("flags"), dict)):
            return Finding("thermal/power", Status.UNKNOWN,
                           "Resource telemetry has an invalid schema.")
        if (telemetry.get("fresh") is not True
                or not finite_number(telemetry.get("measured_at"))
                or not finite_number(telemetry.get("age_seconds"))
                or not 0 <= telemetry["age_seconds"] <= MAX_AGE_SECONDS):
            return Finding("thermal/power", Status.UNKNOWN,
                           "Resource telemetry is stale or invalid.", detail=telemetry)
        flags = telemetry.get("flags") or {}
        mask = telemetry["values"].get("throttled_mask")
        firmware_valid = (type(mask) is int and 0 <= mask <= 0xffffffff
                          and all(type(flags.get(name)) is bool
                                  and flags[name] == bool(mask & (1 << bit))
                                  for bit, name in FLAGS.items()))
        if flags and not firmware_valid:
            return Finding("thermal/power", Status.UNKNOWN,
                           "Firmware telemetry is malformed or inconsistent.")
        if mode != "survival" and any(flags.get(name) is True for name in
               ("undervoltage", "frequency_capped", "throttled", "soft_temperature_limit")):
            return Finding("thermal/power", Status.DEGRADED,
                           "Firmware reports active power/thermal limits.", detail=telemetry)
        values = telemetry.get("values") or {}
        if mode == "full" and (not finite_number(values.get("cpu_temp_c"))
                               or not -20 <= values["cpu_temp_c"] <= 150
                               or not firmware_valid):
            return Finding("thermal/power", Status.UNKNOWN,
                           "Thermal/power sensor coverage is incomplete.", detail=telemetry)
    valid_axes = {"cpu_temp", "cpu_load", "ram", "battery"}
    # A Governor decision with no measured sensor axes is a scheduling
    # decision, NOT evidence that temperatures, memory or power are healthy.
    if (mode not in ("full", "guarded", "reduced", "survival")
            or not isinstance(observed, tuple) or not observed
            or any(type(name) is not str or name not in valid_axes for name in observed)
            or len(set(observed)) != len(observed)):
        return Finding("thermal/power", Status.UNKNOWN,
                       "Thermal/power readings not verified.",
                       "no valid independently supplied sensor coverage")
    reason = getattr(g, "reason", "")
    if telemetry is not None:
        missing = []
        if values.get("battery_pct") is None:
            missing.append("battery level")
        if values.get("on_battery") is None:
            missing.append("power source")
        if missing:
            reason += "; unmeasured: " + ", ".join(missing)
    st = {"full": Status.OK, "guarded": Status.ATTENTION,
          "reduced": Status.DEGRADED, "survival": Status.ACTION_REQUIRED}[mode]
    summ = {"full": "Measured resources are within limits.",
            "guarded": "Easing off slightly under load.",
            "reduced": "Throttled to manage heat/power.",
            "survival": "Shedding load hard — hot or low battery."}.get(mode, "Unknown resource state.")
    rem = "Improve airflow or move to power; the Governor recovers on its own once it cools/charges." \
        if st in (Status.DEGRADED, Status.ACTION_REQUIRED) else ""
    return Finding("thermal/power", st, summ, reason, {"mode": mode, "telemetry": telemetry}, rem)


def _cap_probe(inp: DoctorInputs, cap: Cap, area: str, *, needed: bool, hint: str = "") -> Finding:
    if inp.graph is None:
        return Finding(area, Status.UNKNOWN, f"Can't assess {area}.", "no capability graph provided")
    ex = inp.graph.explain(cap)
    if ex["available"]:
        return Finding(area, Status.OK, f"{area}: {ex['active_provider']} is providing it.",
                       ex["reason"], {"provider": ex["active_provider"]})
    br = inp.graph.blast_radius(cap)
    st = Status.ACTION_REQUIRED if needed else Status.ATTENTION
    # A non-required capability still gets an actionable hint so "unavailable"
    # never dead-ends the operator ("...unavailable" with no idea what fixes it).
    rem = "Plug in / enable a provider for this capability." if needed else hint
    return Finding(area, st, f"{area} is unavailable.", br["reason"],
                   {"candidates": ex["candidates"], "affected": br["affected"]}, rem)


def probe_capture_radio(inp: DoctorInputs) -> Finding:
    return _cap_probe(inp, Cap.RADIO_WIFI_MONITOR, "capture radio", needed=True)


def probe_location(inp: DoctorInputs) -> Finding:
    # location is useful but not required for most ops → attention, not action
    return _cap_probe(inp, Cap.LOCATION_POSITION, "location", needed=False,
                      hint="attach a USB GPS and set REDUX_GPS to populate "
                           "location, the map, and fox-hunt")


def probe_detectors(inp: DoctorInputs) -> Finding:
    if type(inp.detector_count) is not int or inp.detector_count < 0:
        return Finding("detectors", Status.UNKNOWN, "Can't assess detectors.",
                       "a verified nonnegative detector count was not provided")
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
    lab = bool(s.get("lab_mode"))
    if s["empty"]:
        # In lab mode an empty allowlist is NOT disarmed: scope.authorize approves
        # any concrete target on sight, so offense fires. Reporting "No targets
        # armed / will refuse" there is factually wrong — branch on lab_mode.
        if lab:
            return Finding("scope", Status.OK, "Lab mode armed — approving targets on sight.",
                           "lab mode is on: offensive functions fire on your own gear without an explicit allowlist",
                           {"jobs": s["jobs"], "lab_mode": True})
        return Finding("scope", Status.ATTENTION, "No targets armed.",
                       "the authorized-target scope is empty — offensive functions will refuse until you arm one, or enable lab mode",
                       {"jobs": s["jobs"], "lab_mode": False}, "redux scope add / import / arm-lab, or redux mode lab")
    return Finding("scope", Status.OK,
                   f"{s['active']} authorized target(s) armed"
                   + (f" across jobs {', '.join(s['jobs'])}" if s["jobs"] else "")
                   + (" + lab mode (also approving on sight)" if lab else "") + ".",
                   "offensive functions are aimed at your declared scope",
                   {"active": s["active"], "jobs": s["jobs"], "lab_mode": lab})


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


def with_native_plugins(report, inventory):
    """Add an opt-in native-owner assessment, without sampling or trusting disk claims."""
    inventory = inventory if isinstance(inventory, dict) else {}
    report = report if isinstance(report, dict) else {}
    fresh = inventory.get('runtime_fresh') is True
    rows = inventory.get('plugins')
    schema_valid = isinstance(rows, list) and all(isinstance(row, dict) for row in rows[:12])
    rows = rows if isinstance(rows, list) else []
    requested = [row for row in rows[:12]
                 if isinstance(row, dict) and row.get('requested_enabled') is True and row.get('kind') != 'tool']
    if (inventory.get('availability') != 'inspected' or inventory.get('runtime_error')
            or not inventory.get('runtime_generation') or not fresh or not schema_valid):
        status, summary = Status.UNKNOWN, 'Native plugin runtime evidence unavailable or stale.'
    elif inventory.get('next_offset') is not None or inventory.get('partial') or len(rows) > 12:
        status, summary = Status.UNKNOWN, 'Native plugin assessment covers only a partial inventory.'
    elif any(row.get('active') is not True for row in requested):
        status, summary = Status.DEGRADED, 'Requested native plugins are not all active.'
    else:
        status, summary = Status.OK, 'Requested native plugins confirmed by current runtime owner.'
    finding = Finding('native plugins', status, summary,
                      'Current process generation and requested-state revision; tools remain manual.',
                      remediation='Inspect redux packs runtime; review registry, state and local service configuration.')
    raw = report.get('findings')
    findings = []
    for row in raw[:64] if isinstance(raw, list) else []:
        try:
            findings.append(Finding(row['area'], Status(row['status']), row['summary'],
                                    row.get('reason', ''), row.get('detail', {}), row.get('remediation', '')))
        except (KeyError, ValueError, TypeError):
            findings.append(Finding('upstream diagnosis', Status.UNKNOWN, 'Invalid upstream Doctor evidence.'))
    findings.append(finding)
    return Doctor(probes=[lambda inp, value=value: value for value in findings]).report(DoctorInputs())
