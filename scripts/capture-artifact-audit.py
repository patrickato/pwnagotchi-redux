#!/usr/bin/env python3
"""Read-only evidence audit of Redux prepared capture artifacts.

Unlike a row-count dashboard, this verifies that the *ready* artifacts recorded
in jobs.db still exist as regular files with matching SHA-256 digests.
Never reads packet captures, decodes handshake contents, or prints identifiers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote

SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARTIFACT = 33 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


def audit(db: Path, output_dir: Path, *, max_rows=10000):
    result = {"result": "pass", "checked": 0, "issues": {}, "ready_rows": 0}
    def issue(code):
        result["issues"][code] = result["issues"].get(code, 0) + 1
        result["result"] = "attention"

    directory_fd = None
    ledger_fd = None
    # Refuse symlinks for both the SQLite ledger and its artifact directory.
    # The bound matches redux.crack.ingest._MAX_PREPARED_BYTES; older
    # 32 MiB limits would falsely mark valid ingestion output as corrupt.
    try:
        root = output_dir.resolve(strict=True)
        if output_dir.is_symlink() or not root.is_dir():
            issue("output_directory_untrusted")
            return result
        # Pin the verified output directory inode. A pathname-only O_NOFOLLOW
        # on the final artifact cannot protect against a parent directory
        # being renamed and replaced with a symlink during the hash.
        directory_fd = os.open(
            output_dir,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        pinned_dir = os.fstat(directory_fd)
        if not stat.S_ISDIR(pinned_dir.st_mode):
            issue("output_directory_untrusted")
            return result
        if db.is_symlink() or not db.is_file():
            issue("ledger_unavailable")
            return result
        # Pin the ledger inode independently of the SQLite connection. The
        # connection may close after materializing rows, but its former path
        # must not be replaced before the audit publishes PASS.
        ledger_fd = os.open(
            db, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        )
        pinned_ledger = os.fstat(ledger_fd)
        if not stat.S_ISREG(pinned_ledger.st_mode):
            issue("ledger_unavailable")
            return result
        # SQLite mode=ro prevents ledger writes. Explicit BEGIN binds the
        # schema, ready count and ready rows to one read transaction, avoiding
        # a writer changing the observed population between SELECT calls.
        # SQLite may consult existing WAL sidecars; this is not immutable=1.
        uri = "file:" + quote(str(db.absolute()), safe="/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            found = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone()
            if not found:
                issue("schema_missing")
                return result
            count = conn.execute("SELECT COUNT(*) FROM artifacts WHERE status='ready'").fetchone()[0]
            result["ready_rows"] = count
            if count > max_rows:
                issue("row_limit_exceeded")
                return result
            rows = conn.execute("SELECT output_sha,output_path FROM artifacts WHERE status='ready'").fetchall()
            if len(rows) != count:
                issue("audit_unavailable")
                return result
        finally:
            conn.close()
        if count == 0:
            # Integrity cannot be established when no artifacts were prepared.
            result["result"] = "unknown"
            return result
        remaining_budget = MAX_TOTAL_BYTES
        for digest, pathname in rows:
            result["checked"] += 1
            if not isinstance(digest, str) or not SHA.fullmatch(digest):
                issue("invalid_digest")
                continue
            expected = root / (digest + ".hc22000")
            if not isinstance(pathname, str) or Path(pathname) != expected:
                issue("unexpected_artifact_path")
                continue
            try:
                # O_NOFOLLOW and fstat pin the validated artifact inode.
                fd = os.open(expected.name,
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=directory_fd)
                try:
                    meta = os.fstat(fd)
                    if not stat.S_ISREG(meta.st_mode):
                        issue("nonregular_artifact")
                        continue
                    if not 0 < meta.st_size <= MAX_ARTIFACT:
                        issue("artifact_size_invalid")
                        continue
                    if meta.st_size > remaining_budget:
                        issue("audit_byte_budget_exceeded")
                        break
                    remaining_budget -= meta.st_size
                    hashobj = hashlib.sha256()
                    remaining = meta.st_size
                    while remaining:
                        chunk = os.read(fd, min(65536, remaining))
                        if not chunk:
                            break
                        hashobj.update(chunk)
                        remaining -= len(chunk)
                    after = os.fstat(fd)
                    if remaining or (meta.st_dev, meta.st_ino, meta.st_size,
                                     meta.st_mtime_ns, meta.st_ctime_ns) != (
                            after.st_dev, after.st_ino, after.st_size,
                            after.st_mtime_ns, after.st_ctime_ns):
                        issue("artifact_changed_during_audit")
                    elif hashobj.hexdigest() != digest:
                        issue("artifact_digest_mismatch")
                finally:
                    os.close(fd)
            except FileNotFoundError:
                issue("artifact_missing")
            except OSError:
                issue("artifact_unreadable")
        # A pinned fd prevents a redirected read, but cannot by itself prove
        # the originally advertised path still resolves to that directory.
        # Never report PASS for an old inode replaced in the namespace.
        try:
            current_dir = root.lstat()
            if (not stat.S_ISDIR(current_dir.st_mode)
                    or (pinned_dir.st_dev, pinned_dir.st_ino) !=
                    (current_dir.st_dev, current_dir.st_ino)):
                issue("output_directory_changed")
        except OSError:
            issue("output_directory_changed")
        # A reopened or symlinked jobs.db does not identify the same audited
        # ledger, even though the original SQLite rows were internally
        # consistent. WAL writer updates on the same inode remain permitted.
        try:
            current_ledger = db.lstat()
            if (not stat.S_ISREG(current_ledger.st_mode)
                    or (pinned_ledger.st_dev, pinned_ledger.st_ino) !=
                    (current_ledger.st_dev, current_ledger.st_ino)):
                issue("ledger_changed")
        except OSError:
            issue("ledger_changed")
    except (OSError, sqlite3.Error, ValueError):
        issue("audit_unavailable")
    finally:
        if ledger_fd is not None:
            os.close(ledger_fd)
        if directory_fd is not None:
            os.close(directory_fd)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path("/captures/jobs.db"))
    p.add_argument("--output", type=Path, default=Path("/captures/ready"))
    p.add_argument("--receipt", type=Path,
                   help="Opt in to atomic private publication at this explicit path")
    p.add_argument("--inspect-receipt", type=Path,
                   help="Read and classify existing receipt; never scan captures")
    args = p.parse_args(argv)
    if args.inspect_receipt is not None and args.receipt is not None:
        p.error("--inspect-receipt and --receipt cannot be combined")
    # The capture partition must be verified before accessing paths beneath
    # /captures. A missing REDUXCAP must never redirect a receipt to rootfs.
    capture_root = Path("/captures")
    paths = ((args.inspect_receipt,) if args.inspect_receipt is not None
             else (args.db, args.output, args.receipt))
    requires_mount = any(path is not None and
        (path.absolute() == capture_root or capture_root in path.absolute().parents)
        for path in paths)
    mount_identity = None
    if requires_mount or args.receipt is not None or args.inspect_receipt is not None:
        # Direct script invocation uses scripts/ on sys.path, not the checkout.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.inspect_receipt is not None:
        # One bounded cached read, never a SQLite lookup or artifact hash.
        from redux.crack.integrity_receipt import read_cached_receipt
        if requires_mount:
            from redux.storage_mount import capture_mount_snapshot
            if capture_mount_snapshot() is None:
                verdict = {"status": "unknown",
                           "reason": "capture_mount_unverified",
                           "checked": 0, "ready_rows": 0, "age_seconds": None}
            else:
                verdict = read_cached_receipt(args.inspect_receipt)
                if capture_mount_snapshot() is None:
                    verdict = {"status": "unknown",
                               "reason": "capture_mount_unverified",
                               "checked": 0, "ready_rows": 0, "age_seconds": None}
        else:
            verdict = read_cached_receipt(args.inspect_receipt)
        print(json.dumps(verdict, sort_keys=True))
        return {"ok": 0, "attention": 1}.get(verdict["status"], 3)
    if requires_mount:
        from redux.storage_mount import capture_mount_snapshot
        mount_identity = capture_mount_snapshot()
        if mount_identity is None:
            print(json.dumps({"result": "attention",
                              "reason": "capture_mount_unverified"}, sort_keys=True))
            return 2
    report = audit(args.db, args.output)
    if requires_mount and capture_mount_snapshot() != mount_identity:
        print(json.dumps({"result": "attention",
                          "reason": "capture_mount_changed"}, sort_keys=True))
        return 2
    if args.receipt is not None:
        from redux.crack.integrity_receipt import publish_receipt
        try:
            receipt = publish_receipt(args.receipt, report, completed_utc=time.time())
        except (OSError, ValueError, TypeError):
            print(json.dumps({"result": "attention",
                              "reason": "receipt_publication_failed"}, sort_keys=True))
            return 2
    else:
        receipt = {"schema": 1, "completed_utc": time.time(), **report}
    print(json.dumps(receipt, sort_keys=True))
    return 0 if report["result"] == "pass" else 3 if report["result"] == "unknown" else 1


if __name__ == "__main__":
    raise SystemExit(main())
