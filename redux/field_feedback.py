"""Privacy-bounded engineering facts from a saved, validated field inspection.

This module cannot contact hardware. Inputs are projected, unsigned observations,
not attested device facts or independent acceptance results.
"""
from __future__ import annotations
import json

FORMAT = "redux-field-feedback-v1"
STATUS = ("PASS", "FAIL", "UNKNOWN")
MODES = ("managed", "monitor", "unknown")
TRANSPORTS = ("onboard", "usb", "unknown")
BLOCKERS = ("FRAMEBUFFER MISSING", "ILI9486 MISSING", "TOUCH DEVICE MISSING",
            "FRAMEBUFFER AMBIGUOUS", "ILI9486 AMBIGUOUS", "TOUCH DEVICE AMBIGUOUS",
            "ILI9486 DRIVER UNBOUND", "TOUCH CAPABILITIES MISSING",
            "INVENTORY TRUNCATED", "INVENTORY INVALID")
STATES = ("running", "starting", "starting_engine", "rotating", "degraded",
          "telemetry_degraded", "storage_paused", "stopped", "lease_blocked",
          "termination_pending")


def bounded_integer(value, maximum=10**9):
    return value if type(value) is int and 0 <= value <= maximum else None


def choice(value, values):
    return value if type(value) is str and value in values else None


def boolean(value):
    return value if type(value) is bool else None


def facts(projected):
    """Return only audited keys/enum values, never pass through arbitrary fields."""
    if (not isinstance(projected, dict) or projected.get("schema") != 1
            or projected.get("result") != "UNKNOWN"
            or projected.get("physical_acceptance") != "UNKNOWN"
            or projected.get("evidence_origin") != "saved_unsigned_session"):
        raise ValueError("unverified_projection")
    obs = projected.get("observations")
    checks = projected.get("checks")
    if not isinstance(obs, dict) or not isinstance(checks, list) or len(checks) > 40:
        raise ValueError("unverified_observation_shape")
    freshness = "UNKNOWN"
    for row in checks:
        if not isinstance(row, dict):
            raise ValueError("invalid_check_row")
        if row.get("gate") == "saved_observation_freshness":
            freshness = choice(row.get("status"), STATUS) or "UNKNOWN"
            break

    radio_rows = obs.get("radio_inventory_details")
    radios = []
    if isinstance(radio_rows, list):
        for row in radio_rows[:8]:
            if not isinstance(row, dict):
                continue
            radios.append({
                "ordinal": bounded_integer(row.get("radio"), 64),
                "transport": choice(row.get("transport"), TRANSPORTS),
                "kernel_mode": choice(row.get("kernel_mode"), MODES),
                "link_type": bounded_integer(row.get("arphrd"), 65535),
                "network_manager_managed": boolean(row.get("nm_managed")),
                "network_manager_state": bounded_integer(row.get("nm_state"), 120),
                "identity_stable_at_collection": boolean(row.get("identity_stable")),
                "conflicting_process_count": bounded_integer(row.get("conflicting_processes"), 256),
            })
    ledger = obs.get("capture_processing")
    capture = {"worker_available": None, "worker_fresh_at_collection": None,
               "ledger_total": None, "by_status": None, "last_scan": None}
    if isinstance(ledger, dict):
        capture["worker_available"] = boolean(ledger.get("available"))
        capture["worker_fresh_at_collection"] = boolean(ledger.get("fresh_at_collection"))
        capture["ledger_total"] = bounded_integer(ledger.get("artifact_count"))
        allowed = ("ready", "invalid", "error", "duplicate", "deferred", "skipped",
                   "deferred_low_storage", "paused_low_storage", "storage_error")
        counts = ledger.get("by_status")
        if (isinstance(counts, dict) and len(counts) <= len(allowed)
                and all(k in allowed and bounded_integer(v) is not None
                        for k, v in counts.items())
                and capture["ledger_total"] is not None
                and sum(counts.values()) == capture["ledger_total"]):
            capture["by_status"] = {k: counts[k] for k in sorted(counts)}
        scan = ledger.get("last_scan")
        if isinstance(scan, dict):
            scan_count = bounded_integer(scan.get("scanned"))
            outcomes = scan.get("outcomes")
            if (scan_count is not None and isinstance(outcomes, dict)
                    and len(outcomes) <= len(allowed)
                    and all(k in allowed and bounded_integer(v) is not None
                            for k, v in outcomes.items())
                    and (sum(outcomes.values()) == scan_count or
                         (scan_count == 0 and outcomes == {"paused_low_storage": 1}))):
                capture["last_scan"] = {"scanned": scan_count,
                    "outcomes": {k: outcomes[k] for k in sorted(outcomes)}}

    display_state = choice(obs.get("display_metadata_state"),
                           ("BLOCKED", "UNAVAILABLE", "INVALID", "READY_FOR_OPERATOR_SELECTION"))
    blockers = obs.get("display_blockers")
    blockers = sorted(set(k for k in blockers[:16] if type(k) is str and k in BLOCKERS)) if isinstance(blockers, list) else []
    runtime = obs.get("live_runtime")
    runtime_state = choice(runtime.get("live_state"), STATES) if isinstance(runtime, dict) else None
    available = obs.get("captures_available_mib")
    free_mib = bounded_integer(available, 1_000_000)
    free_band = ("under_64_mib" if free_mib is not None and free_mib < 64 else
                 "64_to_511_mib" if free_mib is not None and free_mib < 512 else
                 "512_mib_or_more" if free_mib is not None else None)
    signals = []
    if any(r["transport"] == "onboard" and r["kernel_mode"] == "monitor"
           and r["link_type"] == 1 for r in radios):
        signals.append("onboard_monitor_ethernet_link_type")
    if any(r["transport"] == "usb" and r["link_type"] in (801, 803) for r in radios):
        signals.append("usb_radiotap_not_reception_proof")
    if any(r["network_manager_managed"] is True for r in radios):
        signals.append("network_manager_ownership_requires_review")
    if display_state == "BLOCKED":
        signals.append("display_metadata_blocked")
    if capture["by_status"] is not None and capture["by_status"].get("ready", 0):
        signals.append("ready_ledger_entry_needs_byte_audit")
    return {
        "schema": 1, "format": FORMAT,
        "provenance": "saved_unsigned_read_only_observation",
        "snapshot_freshness": freshness,
        "physical_acceptance": "UNKNOWN",
        "release_qualification": "UNKNOWN",
        "radios": radios,
        "display": {"state": display_state, "blockers": blockers},
        "processing": capture,
        "storage_free_band": free_band,
        "runtime_state_at_collection": runtime_state,
        "signals": signals,
        "does_not_prove": ["native_wifi_reception", "handshake_authenticity",
                           "physical_touch", "rollback_recovery"],
    }


def encoded_facts(projected):
    """Compact but bounded JSON; disallow nested private source strings."""
    output = json.dumps(facts(projected), sort_keys=True, allow_nan=False)
    if len(output.encode()) > 8192:
        raise ValueError("feedback_too_large")
    return output
