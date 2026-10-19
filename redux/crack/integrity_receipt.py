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
# SQLite COUNT(*) is a signed 64-bit integer. The auditor stops before reading
# more than MAX_ROWS ready records, but the population can exceed that bound.
MAX_READY_ROWS = (1 << 63) - 1
GLOBAL_ISSUES = frozenset({
    'output_directory_untrusted', 'output_directory_changed',
    'ledger_unavailable', 'ledger_changed', 'schema_missing', 'row_limit_exceeded',
    'audit_unavailable', 'audit_byte_budget_exceeded',
})
ISSUE_CODES = frozenset({
    'output_directory_untrusted', 'output_directory_changed',
    'ledger_unavailable', 'ledger_changed', 'schema_missing',
    'row_limit_exceeded', 'invalid_digest', 'unexpected_artifact_path',
    'nonregular_artifact', 'artifact_size_invalid', 'artifact_changed_during_audit',
    'artifact_digest_mismatch', 'artifact_missing', 'artifact_unreadable',
    'audit_unavailable', 'audit_byte_budget_exceeded',
})


def _integer(value, maximum=MAX_ROWS):
    return type(value) is int and 0 <= value <= maximum


def _finite_epoch(value):
    """Reject booleans, nonfinite values and giant integers before float math."""
    return (type(value) in (int, float) and 0 <= value <= 1e12
            and math.isfinite(value))


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
    if not (_finite_epoch(now)
            and type(max_age) in (int, float) and 0 < max_age <= 86400
            and math.isfinite(max_age)):
        return unknown
    if not isinstance(receipt, dict) or set(receipt) != {
            'schema', 'completed_utc', 'result', 'checked', 'ready_rows', 'issues'}:
        return unknown
    if type(receipt['schema']) is not int or receipt['schema'] != 1:
        return unknown
    stamp = receipt['completed_utc']
    if not _finite_epoch(stamp):
        return {**unknown, 'reason': 'invalid_timestamp'}
    if stamp > now + 30:
        return {**unknown, 'reason': 'invalid_timestamp'}
    age = max(0, now - stamp)
    if age > max_age:
        return {**unknown, 'reason': 'stale_receipt', 'age_seconds': age}
    checked, ready = receipt['checked'], receipt['ready_rows']
    if not (_integer(checked) and _integer(ready, MAX_READY_ROWS)
            and checked <= ready):
        return unknown
    issues = receipt['issues']
    if (not isinstance(issues, dict) or len(issues) > len(ISSUE_CODES)
            or any(code not in ISSUE_CODES or not _integer(count) or count == 0
                   for code, count in issues.items())):
        return unknown
    # One row can produce at most one row-local problem, but the auditor can
    # also record a global error after a row failed (e.g. output dir replaced).
    row_issues = sum(count for code, count in issues.items()
                     if code not in GLOBAL_ISSUES)
    if (row_issues > checked
            or any(count != 1 for code, count in issues.items()
                   if code in GLOBAL_ISSUES)
            or ('row_limit_exceeded' in issues
                and (ready <= MAX_ROWS or checked != 0))):
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
    """Load a private receipt from a pinned directory, without a scan.

    Every unavailable, invalid, nonregular, in-place modified, or stale
    receipt is classified as UNKNOWN; full JSON contents are never logged.
    """
    unknown = {'status': 'unknown', 'reason': 'receipt_unavailable',
               'checked': 0, 'ready_rows': 0, 'age_seconds': None}
    directory_fd = None
    try:
        source = Path(path)
        if not source.name:
            return unknown
        directory_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY |
                               os.O_NOFOLLOW | os.O_CLOEXEC)
        directory = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory.st_mode) or directory.st_mode & 0o022:
            return unknown
        fd = os.open(source.name, os.O_RDONLY | os.O_NONBLOCK |
                     os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_RECEIPT_BYTES:
                return unknown
            payload = bytearray()
            while len(payload) <= MAX_RECEIPT_BYTES:
                piece = os.read(fd, MAX_RECEIPT_BYTES + 1 - len(payload))
                if not piece:
                    break
                payload.extend(piece)
            after = os.fstat(fd)
            if (len(payload) > MAX_RECEIPT_BYTES or
                    (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                return unknown

            def pairs(items):
                d = {}
                for key, value in items:
                    if key in d:
                        raise ValueError('duplicate key')
                    d[key] = value
                return d

            receipt = json.loads(payload.decode('utf-8'), object_pairs_hook=pairs,
                                 parse_constant=lambda _: (_ for _ in ()).throw(
                                     ValueError('nonfinite')))
        finally:
            os.close(fd)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return unknown
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return classify_receipt(receipt, now=now)

def publish_receipt(path, report, *, completed_utc=None):
    """Write a private audit receipt atomically through a pinned directory.

    Requires a trusted, owner-controlled destination directory. This operation
    never reads capture artifacts or performs a hash scan. Both fsync calls
    matter: replacing a name is atomic, but durable publication also needs a
    directory sync after the rename.
    """
    import secrets

    stamp = time.time() if completed_utc is None else completed_utc
    if not isinstance(report, dict) or set(report) != {
            'result', 'checked', 'ready_rows', 'issues'}:
        raise ValueError('invalid audit result')
    payload = {'schema': 1, 'completed_utc': stamp, **report}
    if classify_receipt(payload, now=stamp)['reason'] in (
            'unverified_receipt', 'invalid_timestamp', 'stale_receipt'):
        raise ValueError('contradictory audit result')
    try:
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('invalid receipt serialization') from exc
    if not 0 < len(encoded) <= MAX_RECEIPT_BYTES:
        raise ValueError('receipt exceeds size limit')

    destination = Path(path)
    if not destination.name or destination.name in ('.', '..'):
        raise ValueError('invalid receipt destination')
    # A directory fd pins the publish location across replacement/rename.
    # O_NOFOLLOW refuses a symlink at the final directory component. The
    # caller must also control all ancestor directories.
    directory_fd = os.open(destination.parent, os.O_RDONLY |
                           os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary_name = None
    try:
        directory = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory.st_mode) or directory.st_mode & 0o022:
            raise ValueError('receipt directory is not private')
        try:
            previous = os.stat(destination.name, dir_fd=directory_fd,
                               follow_symlinks=False)
        except FileNotFoundError:
            previous = None
        if previous is not None and not stat.S_ISREG(previous.st_mode):
            raise ValueError('unsafe existing receipt destination')

        temporary_name = '.receipt-' + secrets.token_hex(16)
        file_fd = os.open(temporary_name,
                          os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                          os.O_NOFOLLOW | os.O_CLOEXEC,
                          0o600, dir_fd=directory_fd)
        with os.fdopen(file_fd, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # renameat does not follow a symlink destination, and both names
        # remain anchored to the originally verified directory inode.
        os.replace(temporary_name, destination.name,
                   src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary_name = None
        os.fsync(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)
    return payload
