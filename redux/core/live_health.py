"""Tie the existing Doctor's diagnosis to measured live supervisor state.

This adapter observes only; it does not restart services, change radio settings
or guess about unobserved hardware. Every finding has an operator-facing reason.
"""
from __future__ import annotations

from .doctor import Doctor, DoctorInputs, Status

_RANK = {"ok": 0, "attention": 1, "degraded": 2, "action": 3}
_LABEL = {
    "ok": "OK", "attention": "ATTENTION", "degraded": "DEGRADED",
    "action": "ACTION REQUIRED", "unknown": "UNKNOWN",
}


def _finding(area, status, summary, reason="", remediation=""):
    return {
        "area": area, "status": status, "summary": summary,
        "reason": reason, "remediation": remediation, "detail": {},
    }


def describe_live_health(state, *, iface="", free_bytes=None, reserve_bytes=0,
                         handoffs=0, last_error="", handoff_error="",
                         doctor_report=None, dashboard_enabled=False,
                         dashboard_active=False, dashboard_error="",
                         sighting_pending=None, sighting_write_error="",
                         sighting_lost=0):
    """Combine Augur's real Doctor report with physical supervisor observations.

    Without Augur the Doctor's normal probes are UNKNOWN, *never* silently OK.
    A healthy parent daemon alone does not prove a successful capture or a
    working converter. Hand-off verification is similarly unknown until one
    closed PCAP was actually published.
    """
    if doctor_report is None:
        doctor_report = Doctor().report(DoctorInputs())
    findings = [dict(f) for f in doctor_report["findings"]]
    if state == "running" and iface:
        findings.append(_finding("live engine", "ok",
                                 "Passive capture supervisor is running.",
                                 f"Bettercap owner is active on {iface}."))
    elif state in {"starting", "starting_engine", "rotating"}:
        findings.append(_finding("live engine", "attention",
                                 "Capture engine is not yet ready.",
                                 f"Live runtime state: {state}.",
                                 "Inspect engine startup if it does not progress."))
    elif state == "storage_paused":
        findings.append(_finding("live engine", "action",
                                 "Capture paused to protect the storage reserve.",
                                 last_error or "Capture storage is low.",
                                 "Free storage or move old captures to another device."))
    elif state == "stopped":
        findings.append(_finding("live engine", "attention",
                                 "Live runtime is stopped, not capturing.",
                                 "No running engine exists to assess.",
                                 "Start redux-live.service when passive capture is wanted."))
    else:
        findings.append(_finding("live engine", "degraded",
                                 "Capture engine is not operational.",
                                 last_error or f"Live runtime state: {state}.",
                                 "Review redux-live.service and the read-only device preflight."))

    if free_bytes is None:
        findings.append(_finding("capture storage", "unknown",
                                 "Capture storage space could not be measured.",
                                 "The filesystem free-space probe was unavailable.",
                                 "Check whether /captures is mounted and writable."))
    elif free_bytes < reserve_bytes:
        findings.append(_finding("capture storage", "action",
                                 "Capture storage reserve is exhausted.",
                                 f"{free_bytes} bytes free; configured minimum is {reserve_bytes}.",
                                 "Review the retention dry run or expand capture storage."))
    else:
        findings.append(_finding("capture storage", "ok",
                                 "Capture storage has room.",
                                 f"{free_bytes} bytes free; reserve is {reserve_bytes}."))

    if handoff_error:
        findings.append(_finding("capture handoff", "degraded",
                                 "A completed capture could not be delivered.",
                                 handoff_error,
                                 "Check /captures/active, /captures/incoming and filesystem health."))
    elif handoffs > 0:
        findings.append(_finding("capture handoff", "ok",
                                 "A completed capture reached the processing queue.",
                                 f"{handoffs} closed capture session(s) handed off."))
    else:
        findings.append(_finding("capture handoff", "unknown",
                                 "No completed capture handoff observed yet.",
                                 "The engine may still be on its first capture session."))

    # Persistence is assessed separately from a healthy engine process:
    # being online is not proof the sightings were committed.
    if sighting_lost > 0:
        findings.append(_finding("sighting persistence", "action",
                                 "Uncommitted observations were lost during a restart.",
                                 f"{sighting_lost} observed record(s) could not be committed.",
                                 "Inspect SQLite/storage faults; retain source captures for analysis."))
    elif sighting_write_error:
        findings.append(_finding("sighting persistence", "degraded",
                                 "Sighting database writes are failing; retry pending.",
                                 sighting_write_error,
                                 "Check /captures free space and SQLite database health."))
    elif sighting_pending is None:
        findings.append(_finding("sighting persistence", "unknown",
                                 "No sighting persistence measurements available.",
                                 "Live Augur has not connected to its database yet."))
    elif sighting_pending > 0:
        findings.append(_finding("sighting persistence", "attention",
                                 "Observed sightings are waiting for commit.",
                                 f"{sighting_pending} record(s) buffered in memory."))
    else:
        findings.append(_finding("sighting persistence", "ok",
                                 "Sighting persistence queue is clear.",
                                 "No unsaved sightings remain in the in-memory queue."))

    if dashboard_enabled:
        if dashboard_active:
            findings.append(_finding("local dashboard", "ok",
                                     "Local diagnostic dashboard is available.",
                                     "The HTTP server is bound to loopback."))
        elif dashboard_error:
            findings.append(_finding("local dashboard", "degraded",
                                     "Local diagnostic dashboard is unavailable.",
                                     dashboard_error,
                                     "Check port conflicts and the redux-live service logs."))
        else:
            findings.append(_finding("local dashboard", "attention",
                                     "Local dashboard has not started yet.",
                                     "No HTTP listener is currently active."))

    assessed = [f["area"] for f in findings if f["status"] != "unknown"]
    gaps = [f["area"] for f in findings if f["status"] == "unknown"]
    known = [f["status"] for f in findings if f["status"] in _RANK]
    worst = max(known, key=lambda status: _RANK[status]) if known else "unknown"
    return {
        "overall": worst,
        "label": _LABEL[worst],
        "findings": findings,
        "coverage": {
            "assessed": assessed, "not_assessed": gaps,
            "reason": (f"{len(gaps)} area(s) could not be assessed: "
                       + ", ".join(gaps) if gaps else "all probed areas were assessed"),
        },
    }
