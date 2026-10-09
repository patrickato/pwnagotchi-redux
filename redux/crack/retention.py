"""Operator-controlled source-capture retention.

A dry run is the default. Never remove unprocessed, invalid, recently processed,
symlinked, multiply-linked or unverifiable source captures. Prepared artifacts
and the conversion ledger are always preserved.
"""
from __future__ import annotations

from contextlib import nullcontext
import fcntl
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from .ingest import Settings

_SHA = re.compile(r"^[0-9a-f]{64}$")
_RAW = frozenset({".pcap", ".pcapng", ".cap"})


def _digest_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            return None
        sha = hashlib.sha256()
        with os.fdopen(os.dup(fd), "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            return None
        return sha.hexdigest(), after
    finally:
        os.close(fd)


def _eligible(row, settings, cutoff):
    source_sha, source_path, output_sha, output_path, status, updated_at = row
    if status not in {"ready", "duplicate"}:
        return None
    if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
        return None
    if not isinstance(output_sha, str) or not _SHA.fullmatch(output_sha):
        return None
    if updated_at > cutoff:
        return None
    source = Path(source_path)
    output = Path(output_path) if output_path else None
    # Only immediate children of configured, nonsymlinked source directories.
    if (not source.is_absolute() or source.suffix.lower() not in _RAW
            or source.parent not in settings.inputs
            or any(p.is_symlink() for p in settings.inputs)
            or output != settings.output_dir / (output_sha + ".hc22000")
            or settings.output_dir.is_symlink()):
        return None
    try:
        st = source.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_size == 0 or st.st_mtime > cutoff):
            return None
        saved = output.lstat()
        if not stat.S_ISREG(saved.st_mode):
            return None
        read_source = _digest_regular(source)
        read_output = _digest_regular(output)
        if (read_source is None or read_output is None
                or read_source[0] != source_sha or read_output[0] != output_sha):
            return None
        return source, read_source[1]
    except (OSError, ValueError):
        return None


def retention_report(settings: Settings, *, apply=False, older_than_days=30,
                     max_files=100, clock=time.time):
    """Inspect bounded candidates; deletion requires explicit apply=True.

    Uses the same ingestion-worker flock to prevent a concurrent ingestion pass.
    Checks both source and prepared artifact hashes, ensuring no data is removed
    if a result was lost or altered. The operator must intentionally opt in.
    """
    if not 7 <= older_than_days <= 3650:
        raise ValueError("retention age must be between 7 and 3650 days")
    if not 1 <= max_files <= 1000:
        raise ValueError("retention max_files must be between 1 and 1000")
    if settings.database.is_symlink() or settings.output_dir.is_symlink():
        raise ValueError("retention database/output symlinks are forbidden")
    report = {"mode": "apply" if apply else "dry_run", "age_days": older_than_days,
              "checked": 0, "eligible": 0, "reclaimable_bytes": 0,
              "removed": 0, "reclaimed_bytes": 0, "candidates": [],
              "note": "Prepared artifacts and unverified captures are never deleted."}
    if not settings.database.is_file():
        return report
    cutoff = clock() - older_than_days * 86400
    lock_path = settings.database.with_suffix(".lock")
    # A new dry run must not create a file. Existing workers share their flock.
    locking = lock_path.open("a+b") if (apply or lock_path.is_file()) else nullcontext(None)
    with locking as lock:
        if lock is not None:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        conn = sqlite3.connect(settings.database.as_uri() + "?mode=ro",
                               uri=True, timeout=5)
        try:
            candidates = conn.execute(
                """SELECT source_sha, source_path, output_sha, output_path,
                          status, updated_at
                   FROM artifacts
                   WHERE updated_at <= ? AND status IN ('ready','duplicate')
                   ORDER BY updated_at ASC
                   LIMIT ?""", (cutoff, max_files * 10)
            )
            for row in candidates:
                report["checked"] += 1
                found = _eligible(row, settings, cutoff)
                if found is None:
                    continue
                path, checked_st = found
                report["eligible"] += 1
                report["reclaimable_bytes"] += checked_st.st_size
                report["candidates"].append(str(path))
                if apply:
                    # Ensure the directory entry has not changed since hashing.
                    try:
                        st = path.lstat()
                        if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                            st.st_nlink) != (
                            checked_st.st_dev, checked_st.st_ino,
                            checked_st.st_size, checked_st.st_mtime_ns, 1
                        ):
                            continue
                        path.unlink()
                        report["removed"] += 1
                        report["reclaimed_bytes"] += st.st_size
                    except OSError:
                        continue
                if report["eligible"] >= max_files:
                    break
        finally:
            conn.close()
    return report
