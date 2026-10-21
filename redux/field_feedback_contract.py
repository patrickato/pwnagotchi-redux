"""Strict, no-free-text boundary for Codex-bound field feedback.

The offline converter is an independent process. Do not assume its stdout
is safe merely because the subprocess returned zero: verify the complete
fixed-schema output before placing any bytes in a model prompt.
"""
from __future__ import annotations

import json

FORMAT = "redux-field-feedback-v1"
PROVENANCE = "saved_unsigned_read_only_observation"
NEGATIVE_GATES = ["native_wifi_reception", "handshake_authenticity",
                  "physical_touch", "rollback_recovery"]
SIGNALS = ("onboard_monitor_ethernet_link_type",
           "usb_radiotap_not_reception_proof",
           "network_manager_ownership_requires_review",
           "display_metadata_blocked",
           "ready_ledger_entry_needs_byte_audit")
RADIO_KEYS = frozenset(("ordinal", "transport", "kernel_mode", "link_type",
    "network_manager_managed", "network_manager_state",
    "identity_stable_at_collection", "conflicting_process_count"))
PROCESSING_KEYS = frozenset(("worker_available", "worker_fresh_at_collection",
                            "ledger_total", "by_status", "last_scan"))
COUNTS = frozenset(("ready", "invalid", "error", "duplicate", "deferred",
                    "skipped", "deferred_low_storage", "paused_low_storage",
                    "storage_error"))
BLOCKERS = frozenset(("FRAMEBUFFER MISSING", "ILI9486 MISSING",
    "TOUCH DEVICE MISSING", "FRAMEBUFFER AMBIGUOUS", "ILI9486 AMBIGUOUS",
    "TOUCH DEVICE AMBIGUOUS", "ILI9486 DRIVER UNBOUND",
    "TOUCH CAPABILITIES MISSING", "INVENTORY TRUNCATED", "INVENTORY INVALID"))
TOP_KEYS = frozenset(("schema", "format", "provenance", "snapshot_freshness",
    "physical_acceptance", "release_qualification", "radios", "display",
    "processing", "storage_free_band", "runtime_state_at_collection",
    "signals", "does_not_prove"))
STATES = frozenset(("running", "starting", "starting_engine", "rotating",
    "degraded", "telemetry_degraded", "storage_paused", "stopped",
    "lease_blocked", "termination_pending"))


def _shape(value, keys):
    if type(value) is not dict or value.keys() != keys:
        raise ValueError("invalid_feedback_shape")


def _choice(value, allowed):
    if value is not None and (type(value) is not str or value not in allowed):
        raise ValueError("invalid_feedback_category")


def _count(value, maximum):
    if value is not None and (type(value) is not int or not 0 <= value <= maximum):
        raise ValueError("invalid_feedback_count")


def _boolean(value):
    if value is not None and type(value) is not bool:
        raise ValueError("invalid_feedback_boolean")


def _counts(value, *, total=None, scan=False):
    if value is None:
        return
    if type(value) is not dict or len(value) > len(COUNTS):
        raise ValueError("invalid_feedback_ledger")
    for key, number in value.items():
        if type(key) is not str or key not in COUNTS:
            raise ValueError("invalid_feedback_ledger_category")
        _count(number, 10**9)
        if number is None:
            raise ValueError("invalid_feedback_ledger_count")
    if total is None:
        raise ValueError("missing_feedback_ledger_total")
    observed = sum(value.values())
    if observed != total and not (
        scan and total == 0 and value == {"paused_low_storage": 1}
    ):
        raise ValueError("inconsistent_feedback_ledger")


def validate_and_encode_feedback(payload):
    """Reject arbitrary output, returning only validated, bounded JSON.

    A validation failure must never fall back to raw subprocess output.
    """
    _shape(payload, TOP_KEYS)
    if (type(payload["schema"]) is not int or payload["schema"] != 1
            or payload["format"] != FORMAT
            or payload["provenance"] != PROVENANCE
            or payload["physical_acceptance"] != "UNKNOWN"
            or payload["release_qualification"] != "UNKNOWN"
            or payload["does_not_prove"] != NEGATIVE_GATES):
        raise ValueError("invalid_feedback_provenance")
    _choice(payload["snapshot_freshness"], ("PASS", "FAIL", "UNKNOWN"))
    if type(payload["radios"]) is not list or len(payload["radios"]) > 8:
        raise ValueError("invalid_feedback_radios")
    for radio in payload["radios"]:
        _shape(radio, RADIO_KEYS)
        _count(radio["ordinal"], 64)
        _choice(radio["transport"], ("onboard", "usb", "unknown"))
        _choice(radio["kernel_mode"], ("managed", "monitor", "unknown"))
        _count(radio["link_type"], 65535)
        _boolean(radio["network_manager_managed"])
        _count(radio["network_manager_state"], 120)
        _boolean(radio["identity_stable_at_collection"])
        _count(radio["conflicting_process_count"], 256)
    display = payload["display"]
    _shape(display, frozenset(("state", "blockers")))
    _choice(display["state"], ("BLOCKED", "UNAVAILABLE", "INVALID",
                               "READY_FOR_OPERATOR_SELECTION"))
    blockers = display["blockers"]
    if (type(blockers) is not list or len(blockers) > len(BLOCKERS)
            or any(type(item) is not str or item not in BLOCKERS for item in blockers)
            or blockers != sorted(set(blockers))):
        raise ValueError("invalid_feedback_blockers")
    processing = payload["processing"]
    _shape(processing, PROCESSING_KEYS)
    _boolean(processing["worker_available"])
    _boolean(processing["worker_fresh_at_collection"])
    _count(processing["ledger_total"], 10**9)
    _counts(processing["by_status"], total=processing["ledger_total"])
    scan = processing["last_scan"]
    if scan is not None:
        _shape(scan, frozenset(("scanned", "outcomes")))
        _count(scan["scanned"], 10**9)
        if scan["scanned"] is None:
            raise ValueError("missing_feedback_scan_total")
        _counts(scan["outcomes"], total=scan["scanned"], scan=True)
        if scan["outcomes"] is None:
            raise ValueError("missing_feedback_scan_outcomes")
    _choice(payload["storage_free_band"],
            ("under_64_mib", "64_to_511_mib", "512_mib_or_more"))
    _choice(payload["runtime_state_at_collection"], STATES)
    signals = payload["signals"]
    if type(signals) is not list or len(signals) > len(SIGNALS):
        raise ValueError("invalid_feedback_signals")
    expected = []
    radios = payload["radios"]
    if any(r["transport"] == "onboard" and r["kernel_mode"] == "monitor"
           and r["link_type"] == 1 for r in radios):
        expected.append(SIGNALS[0])
    if any(r["transport"] == "usb" and r["link_type"] in (801, 803) for r in radios):
        expected.append(SIGNALS[1])
    if any(r["network_manager_managed"] is True for r in radios):
        expected.append(SIGNALS[2])
    if display["state"] == "BLOCKED":
        expected.append(SIGNALS[3])
    if processing["by_status"] is not None and processing["by_status"].get("ready", 0):
        expected.append(SIGNALS[4])
    if signals != expected:
        raise ValueError("invalid_feedback_signals")
    try:
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_feedback_json") from exc
    if len(encoded.encode("utf-8")) > 8192:
        raise ValueError("feedback_too_large")
    return encoded
