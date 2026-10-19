"""Bounded, read-only interpretation of offline capture artifact audit receipts.

This module does not run a scan or read capture data. A separate scheduled or
manual audit may publish its JSON result; consumers can classify it cheaply.
An unverifiable receipt NEVER turns into evidence that artifacts are intact.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import time

MAX_RECEIPT_AGE = 3600
MAX_ROWS = 10000
ISSUE_CODES = frozenset({
    'output_directory_untrusted', 'ledger_unavailable', 'schema_missing',
    'row_limit_exceeded', 'invalid_digest', 'unexpected_artifact_path',
    'nonregular_artifact', 'artifact_size_invalid', 'artifact_changed_during_audit',
    'artifact_digest_mismatch', 'artifact_missing', 'artifact_unreadable',
    'audit_unavailable', 'audit_byte_budget_exceeded',
})


def _integer(value, maximum=MAX_ROWS):
    return type(value) is int and 0 <= value <= maximum


def classify_receipt(receipt, *, now=None, max_age=MAX_RECEIPT_AGE):
    """Classify a saved result. Returns only known enums and bounded counts.

    Receipt shape: {schema:1, completed_utc:<epoch>, result:<enum>,
                    checked:<int>, ready_rows:<int>, issues:{code:<count>}}.
    Replays and stale results are UNKNOWN, not proof of current integrity.
    """
    unknown = {'status': 'unknown', 'reason': 'unverified_receipt',
               'checked': 0, 'ready_rows': 0, 'age_seconds': None}
    if now is None:
        now = time.time()
    if not (type(now) in (int, float) and math.isfinite(now)
            and type(max_age) in (int, float) and math.isfinite(max_age)
            and 0 < max_age <= 86400):
        return unknown
    if not isinstance(receipt, dict) or set(receipt) != {
            'schema', 'completed_utc', 'result', 'checked', 'ready_rows', 'issues'}:
        return unknown
    if type(receipt['schema']) is not int or receipt['schema'] != 1:
        return unknown
    stamp = receipt['completed_utc']
    if type(stamp) not in (int, float) or not math.isfinite(stamp):
        return unknown
    if stamp < 0 or stamp > now + 30:
        return {**unknown, 'reason': 'invalid_timestamp'}
    age = max(0, now - stamp)
    if age > max_age:
        return {**unknown, 'reason': 'stale_receipt', 'age_seconds': age}
    checked, ready = receipt['checked'], receipt['ready_rows']
    if not (_integer(checked) and _integer(ready) and checked <= ready):
        return unknown
    issues = receipt['issues']
    if (not isinstance(issues, dict) or len(issues) > len(ISSUE_CODES)
            or any(code not in ISSUE_CODES or not _integer(count) or count == 0
                   for code, count in issues.items())):
        return unknown
    # Every checked artifact can contribute at most one row-local error;
    # global/preflight errors can exist before any row is inspected.
    # A forged receipt must not turn contradictory counters into attention.
    if checked > 0 and sum(issues.values()) > checked:
        return unknown
    reported = receipt['result']
    if reported == 'pass' and not issues and checked == ready and checked > 0:
        status, reason = 'ok', 'verified_ready_artifacts'
    elif reported == 'unknown' and not issues and ready == checked == 0:
        status, reason = 'unknown', 'no_ready_artifacts'
    elif reported == 'attention' and issues:
        status, reason = 'attention', 'integrity_discrepancies'
    else:
        return unknown
    return {'status': status, 'reason': reason, 'checked': checked,
            'ready_rows': ready, 'age_seconds': age}


MAX_RECEIPT_BYTES = 8192


def read_cached_receipt(path, *, now=None):
    """Load a precomputed receipt using a bounded no-follow read.

    This never hashes an artifact, opens the SQLite capture ledger, or
    publishes a new receipt. Errors always become UNKNOWN.
    """
    unknown = {'status': 'unknown', 'reason': 'receipt_unavailable',
               'checked': 0, 'ready_rows': 0, 'age_seconds': None}
    try:
        source = Path(path)
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK |
                     getattr(os, 'O_NOFOLLOW', 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_RECEIPT_BYTES:
                return unknown
            payload = b''
            while len(payload) <= MAX_RECEIPT_BYTES:
                piece = os.read(fd, MAX_RECEIPT_BYTES + 1 - len(payload))
                if not piece:
                    break
                payload += piece
            if len(payload) > MAX_RECEIPT_BYTES:
                return unknown
            def pairs(items):
                d = {}
                for key, value in items:
                    if key in d:
                        raise ValueError('duplicate key')
                    d[key] = value
                return d
            receipt = json.loads(payload.decode('utf-8'), object_pairs_hook=pairs,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        finally:
            os.close(fd)
    except (OSError, UnicodeError, ValueError, TypeError):
        return unknown
    return classify_receipt(receipt, now=now)


def publish_receipt(path, report, *, completed_utc=None):
    """Atomically publish an audit result to a private regular file.

    The caller must explicitly invoke the expensive audit first. Publication
    never traverses a symlink destination and never touches capture artifacts.
    """
    import tempfile

    stamp = time.time() if completed_utc is None else completed_utc
    if not isinstance(report, dict) or set(report) != {'result', 'checked', 'ready_rows', 'issues'}:
        raise ValueError('invalid audit result')
    payload = {'schema': 1, 'completed_utc': stamp, **report}
    if classify_receipt(payload, now=stamp)['reason'] in ('unverified_receipt', 'invalid_timestamp'):
        raise ValueError('contradictory audit result')
    destination = Path(path)
    parent = destination.parent
    if parent.is_symlink() or not parent.is_dir() or destination.is_symlink():
        raise ValueError('unsafe receipt destination')
    if destination.exists() and not destination.is_file():
        raise ValueError('receipt destination must be regular')
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise ValueError('receipt exceeds size limit')
    temp = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.receipt-', dir=parent, delete=False) as out:
            temp = Path(out.name)
            os.fchmod(out.fileno(), 0o600)
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
        if destination.is_symlink():
            raise ValueError('receipt destination replaced by symlink')
        os.replace(temp, destination)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return payload
