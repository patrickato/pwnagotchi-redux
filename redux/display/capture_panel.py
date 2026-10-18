"""Read-only, bounded capture-processing display model for Redux RGB565 UI.

Uses only the already-supplied /api/status snapshot, never opens the ledger,
touches capture files, starts workers, or implies that a hash was cracked.
"""
from __future__ import annotations

import math
import time

_MAX = (1 << 63) - 1


def _count(value):
    return value if type(value) is int and 0 <= value <= _MAX else None


def _clock(value):
    if type(value) not in (int, float):
        return None
    try:
        if math.isfinite(value) and 0 <= value <= 1e12:
            return float(value)
    except (ValueError, OverflowError, TypeError):
        pass
    return None


def model(report, *, now=None):
    """Conservative capture health independent of Doctor's overall severity.

    A current Redux runtime snapshot (20s) and a current worker heartbeat
    (300s) are separate observations. Ledger totals are snapshot counters, not
    per-second throughput. Missing evidence never turns into zero or PASS.
    """
    now = time.time() if now is None else now
    time_now = _clock(now)
    root = report if isinstance(report, dict) else {}
    runtime = root.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    sampled = _clock(runtime.get("updated_utc"))
    runtime_fresh = (time_now is not None and sampled is not None
                     and -30 <= time_now - sampled <= 20)
    pipeline = root.get("capture_processing")
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    available = pipeline.get("available") is True
    base = {"ledger": ("UNAVAILABLE" if not available else
                       "STALE" if not runtime_fresh else "AVAILABLE"),
            "ready": None, "errors": None, "invalid": None,
            "hash_records": None, "artifacts": None,
            "scan_state": "UNVERIFIED", "scan_files": None,
            "scan_age_seconds": None, "last_ready": None, "last_errors": None,
            "last_deferred": None}
    if not available or not runtime_fresh:
        return base

    status = pipeline.get("by_status")
    status = status if isinstance(status, dict) else {}
    base.update(ready=_count(status.get("ready")),
                errors=_count(status.get("error")),
                invalid=_count(status.get("invalid")),
                hash_records=_count(pipeline.get("hash_records")),
                artifacts=_count(pipeline.get("artifacts")))
    last = pipeline.get("last_scan")
    if not isinstance(last, dict):
        # The ingestion reader distinguishes an absent first heartbeat from
        # corrupt persisted scan metadata through a fixed, sanitized error.
        # Do not turn evidence corruption into an innocuous "never scanned".
        malformed = (last is not None or
                     pipeline.get("scan_error") == "invalid stored scan record")
        base["scan_state"] = "INVALID" if malformed else "NO COMPLETED SCAN"
        return base
    scanned = _count(last.get("scanned"))
    at = _clock(last.get("completed_utc"))
    outcomes = last.get("outcomes")
    if scanned is None or at is None or not isinstance(outcomes, dict):
        base["scan_state"] = "INVALID"
        return base
    age = time_now - at
    if not -30 <= age <= 300:
        base["scan_state"] = "STALE"
        return base
    # The heartbeat reports per-pass worker outcomes separately from the
    # cumulative ledger. Reject invalid counters instead of showing 0.
    wanted = ("ready", "error", "deferred", "deferred_low_storage")
    if any(key in outcomes and _count(outcomes[key]) is None for key in wanted):
        base["scan_state"] = "INVALID"
        return base
    if len(outcomes) > 32 or any(type(k) is not str or len(k) > 80 or
                                  _count(v) is None for k, v in outcomes.items()):
        base["scan_state"] = "INVALID"
        return base
    if outcomes.get("paused_low_storage") is not None:
        if outcomes != {"paused_low_storage": 1} or scanned != 0:
            base["scan_state"] = "INVALID"
            return base
        base.update(scan_state="PAUSED STORAGE", scan_age_seconds=round(max(0, age)))
        return base
    if sum(outcomes.values()) != scanned:
        base["scan_state"] = "INVALID"
        return base
    base.update(scan_state="RECENT", scan_files=scanned,
                scan_age_seconds=round(max(0, age)),
                last_ready=_count(outcomes.get("ready")),
                last_errors=_count(outcomes.get("error")),
                last_deferred=(sum(outcomes.get(k, 0) for k in
                                   ("deferred", "deferred_low_storage"))))
    return base


def compact_count(value):
    """Bound TFT numeric width; UNKNOWN is never rendered as zero."""
    value = _count(value)
    if value is None:
        return "--"
    if value < 10_000:
        return str(value)
    for scale, suffix in ((10**12, "T"), (10**9, "B"),
                          (10**6, "M"), (10**3, "K")):
        if value >= scale:
            display = value / scale
            if display >= 999.5:
                return "999+T" if suffix == "T" else f"999+{suffix}"
            return f"{display:.1f}{suffix}" if display < 10 else f"{display:.0f}{suffix}"
    return str(value)
