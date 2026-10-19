"""Pure shared capture-worker heartbeat invariants, with no ledger/artifact I/O.

Doctor and the native UI reuse this contract while retaining independent age
limits. A completed empty scan is not proof that a capture artifact exists.
"""
import math


def count(value):
    return type(value) is int and 0 <= value <= (1 << 63) - 1


def scan_record_valid(record):
    """Shared worker heartbeat invariant, independent of UI/Doctor age limits."""
    if not isinstance(record, dict):
        return False
    scanned, stamp, outcomes = record.get("scanned"), record.get("completed_utc"), record.get("outcomes")
    if not count(scanned) or not (type(stamp) in (int, float) and 0 <= stamp <= 1e12 and math.isfinite(stamp)) or not isinstance(outcomes, dict) or len(outcomes) > 32:
        return False
    if any(type(key) is not str or len(key) > 80 or not count(value) for key, value in outcomes.items()):
        return False
    if "paused_low_storage" in outcomes:
        return outcomes == {"paused_low_storage": 1} and scanned == 0
    return sum(outcomes.values()) == scanned


def status_counts_valid(summary):
    """Missing totals are unknown; supplied totals must be bounded measurements."""
    totals = summary.get('by_status', {})
    return (isinstance(totals, dict) and len(totals) <= 32
            and all(type(key) is str and len(key) <= 80 and count(value) for key, value in totals.items())
            and all(key not in summary or count(summary[key]) for key in ('artifacts', 'hash_records')))
