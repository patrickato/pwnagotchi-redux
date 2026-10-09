"""Durable, passive capture-file ingestion for standalone Redux.

This worker observes existing capture files; it never controls the radio.
Format checks do not cryptographically prove handshake correctness.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
import fcntl
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
import time
import tomllib

SUFFIXES = frozenset({".pcap", ".pcapng", ".cap", ".hc22000"})
_HEX = re.compile(r"^[0-9a-fA-F]*$")
_MAC = re.compile(r"^[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class Settings:
    inputs: tuple[Path, ...]
    output_dir: Path
    database: Path
    settle_seconds: float = 4.0
    max_capture_bytes: int = 128 * 1024 * 1024
    max_files_per_pass: int = 100
    converter: str = "hcxpcapngtool"
    convert_timeout_seconds: int = 90
    min_free_bytes: int = 64 * 1024 * 1024

    def __post_init__(self):
        if not self.inputs or not all(p.is_absolute() for p in self.inputs):
            raise ValueError("capture inputs must be absolute directories")
        if not self.output_dir.is_absolute() or not self.database.is_absolute():
            raise ValueError("database/output paths must be absolute")
        if self.settle_seconds < 0 or self.max_capture_bytes < 1024:
            raise ValueError("invalid settling/size limit")
        if self.min_free_bytes < 1024 * 1024:
            raise ValueError("min_free_bytes must be at least 1 MiB")
        if self.max_files_per_pass < 1 or not 1 <= self.convert_timeout_seconds <= 3600:
            raise ValueError("invalid scan/timeout limits")

    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            data = tomllib.load(fh).get("pipeline", {})
        return cls(
            inputs=tuple(Path(p) for p in data.get("inputs", [])),
            output_dir=Path(data.get("output_dir", "/captures/ready")),
            database=Path(data.get("database", "/captures/jobs.db")),
            settle_seconds=float(data.get("settle_seconds", 4)),
            max_capture_bytes=int(data.get("max_capture_bytes", 128 * 1024 * 1024)),
            max_files_per_pass=int(data.get("max_files_per_pass", 100)),
            converter=str(data.get("converter", "hcxpcapngtool")),
            convert_timeout_seconds=int(data.get("convert_timeout_seconds", 90)),
            min_free_bytes=int(data.get("min_free_bytes", 64 * 1024 * 1024)),
        )


def _hex(value, minimum, maximum=None):
    return (len(value) >= minimum and len(value) % 2 == 0
            and (maximum is None or len(value) <= maximum)
            and _HEX.fullmatch(value) is not None)


def valid_22000_line(value):
    """Structural screening only; not an EAPOL/PMKID cryptographic validator."""
    p = value.strip().split("*")
    if len(p) != 9 or p[:1] != ["WPA"] or p[1] not in ("01", "02"):
        return False
    if not (_hex(p[2], 32, 32) and _MAC.fullmatch(p[3])
            and _MAC.fullmatch(p[4]) and _hex(p[5], 0, 64)):
        return False
    if p[1] == "01":
        return p[6:] == ["", "", ""]
    return (_hex(p[6], 64, 64) and _hex(p[7], 2, 8192)
            and _hex(p[8], 2, 2))


def normalize_22000(data: bytes):
    """Return structurally screened unique lines and the AP BSSIDs within them."""
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("converted material exceeds 32 MiB")
    lines, bssids = {}, set()
    for raw in data.splitlines():
        try:
            line = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            continue
        if valid_22000_line(line):
            fields = line.split("*")
            lines["*".join([fields[0], fields[1]] +
                           [v.lower() for v in fields[2:]])] = None
            bssids.add(fields[3].lower())
    return list(lines), sorted(bssids)


def free_bytes(path):
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def _digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".redux-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        # Durable directory entry after a sudden power loss on the Pi.
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CaptureIngestor:
    """Idempotent, SQLite-backed ingestor with bounded rotating directory scans."""

    def __init__(self, settings, *, runner=None, clock=time.time):
        self.settings = settings
        self.runner = runner or subprocess.run
        self.clock = clock
        if settings.database.is_symlink() or settings.output_dir.is_symlink():
            raise ValueError("output/database symlinks not permitted")
        settings.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(settings.output_dir, 0o700)
        self.db = sqlite3.connect(settings.database, timeout=10)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS artifacts (
            source_sha TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            output_sha TEXT,
            output_path TEXT,
            status TEXT NOT NULL,
            hashes INTEGER NOT NULL DEFAULT 0,
            bssids TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_status ON artifacts(status)")
        self.db.execute("CREATE TABLE IF NOT EXISTS pipeline_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.db.commit()
        os.chmod(settings.database, 0o600)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def rows(self):
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(artifacts)")]
        return [dict(zip(columns, row)) for row in self.db.execute(
            "SELECT * FROM artifacts ORDER BY created_at")]

    def summary(self):
        grouped = dict(self.db.execute(
            "SELECT status, COUNT(*) FROM artifacts GROUP BY status"))
        count = self.db.execute(
            "SELECT COALESCE(SUM(hashes),0) FROM artifacts WHERE status='ready'"
        ).fetchone()[0]
        return {"artifacts": sum(grouped.values()), "by_status": grouped,
                "hash_records": count}

    def _record(self, sha, source, status, *, reason="", out_sha=None,
                out_path=None, count=0, bssids=()):
        now = self.clock()
        self.db.execute("""INSERT INTO artifacts
          (source_sha,source_path,output_sha,output_path,status,hashes,
           bssids,reason,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(source_sha) DO UPDATE SET
           source_path=excluded.source_path,output_sha=excluded.output_sha,
           output_path=excluded.output_path,status=excluded.status,
           hashes=excluded.hashes,bssids=excluded.bssids,
           reason=excluded.reason,updated_at=excluded.updated_at""",
          (sha, str(source), out_sha, str(out_path) if out_path else None,
           status, count, json.dumps(list(bssids)), reason[:240], now, now))
        self.db.commit()

    def ingest_file(self, source):
        source = Path(source)
        try:
            st = source.lstat()
        except FileNotFoundError:
            return "deferred"
        if not stat.S_ISREG(st.st_mode) or source.suffix.lower() not in SUFFIXES:
            return "skipped"
        if st.st_size == 0 or self.clock() - st.st_mtime < self.settings.settle_seconds:
            return "deferred"
        if st.st_size > self.settings.max_capture_bytes:
            return "skipped"
        if free_bytes(self.settings.output_dir) < self.settings.min_free_bytes:
            return "deferred_low_storage"
        try:
            sha = _digest_file(source)
        except OSError:
            # The writer could have finalized/moved the file during this scan.
            return "deferred"
        existing = self.db.execute(
            "SELECT status, output_sha, output_path FROM artifacts WHERE source_sha=?",
            (sha,),
        ).fetchone()
        if existing and existing[0] == "invalid":
            return "duplicate"
        if existing and existing[0] in {"ready", "duplicate"}:
            prior_sha, prior_path = existing[1], existing[2]
            if prior_sha and prior_path:
                previous = Path(prior_path)
                if previous.is_symlink():
                    self._record(sha, source, "error", reason="prepared artifact became a symlink")
                    return "error"
                if previous.is_file():
                    try:
                        if _digest_file(previous) == prior_sha:
                            return "duplicate"
                    except OSError:
                        return "deferred"
                    self._record(sha, source, "error",
                                 reason="previous prepared artifact was altered")
                    return "error"
                if previous.exists():
                    self._record(sha, source, "error",
                                 reason="prepared output path is not a regular file")
                    return "error"
            # The database claims success but the file vanished (for example an
            # interrupted SD write or operator cleanup). Rebuild from source.
        try:
            if source.suffix.lower() == ".hc22000":
                raw = source.read_bytes()
            else:
                with tempfile.TemporaryDirectory(
                    prefix=".hcx-", dir=self.settings.output_dir
                ) as temp_dir:
                    target = Path(temp_dir) / "converted.hc22000"
                    result = self.runner(
                        [self.settings.converter, "-o", str(target), str(source)],
                        capture_output=True,
                        timeout=self.settings.convert_timeout_seconds, check=False,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(f"hcxpcapngtool returned {result.returncode}")
                    raw = target.read_bytes() if target.exists() else b""
            after = source.lstat()
            if not stat.S_ISREG(after.st_mode) or (
                after.st_size, after.st_mtime_ns
            ) != (st.st_size, st.st_mtime_ns) or _digest_file(source) != sha:
                return "deferred"
            records, bssids = normalize_22000(raw)
            if not records:
                self._record(sha, source, "invalid",
                             reason="no structurally valid 22000 records")
                return "invalid"
            data = ("\n".join(records) + "\n").encode("ascii")
            output_sha = hashlib.sha256(data).hexdigest()
            output_path = self.settings.output_dir / (output_sha + ".hc22000")
            if output_path.exists():
                if (output_path.is_symlink() or not output_path.is_file()
                        or _digest_file(output_path) != output_sha):
                    raise RuntimeError("output collision or altered artifact")
                state = "duplicate"
            else:
                _atomic_write(output_path, data)
                state = "ready"
            self._record(sha, source, state, out_sha=output_sha,
                         out_path=output_path, count=len(records), bssids=bssids)
            return state
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            self._record(sha, source, "error",
                         reason=f"{type(error).__name__}: {str(error)[:160]}")
            return "error"

    def _completed_scan(self, scanned, outcomes, *, cursor_path=None):
        """Commit a durable worker heartbeat, including empty and paused passes.

        It proves the scan returned successfully, NOT that a captured handshake
        was valid. The existing database transaction also persists the cursor.
        """
        heartbeat = {
            "schema": 1, "completed_utc": self.clock(), "scanned": scanned,
            "outcomes": dict(outcomes),
        }
        self.db.execute(
            "INSERT OR REPLACE INTO pipeline_meta(key,value) VALUES('last_scan',?)",
            (json.dumps(heartbeat, separators=(",", ":"), sort_keys=True),),
        )
        if cursor_path is not None:
            # Cursor and heartbeat are committed together, so a crashed pass
            # can retry safely without claiming it completed.
            self.db.execute(
                "INSERT OR REPLACE INTO pipeline_meta(key,value) "
                "VALUES('cursor_path',?)", (cursor_path,),
            )
        self.db.commit()
        return {"scanned": scanned, "outcomes": dict(outcomes),
                "summary": self.summary()}

    def _candidates(self):
        """Yield eligible immediate children without buffering a directory.

        Never recurse into raw captures or follow an incoming-file symlink.
        Concurrent file changes are handled by ingest_file's settle/hash checks.
        """
        for directory in self.settings.inputs:
            if directory.is_dir() and not directory.is_symlink():
                for path in directory.iterdir():
                    if path.suffix.lower() in SUFFIXES and not path.is_symlink():
                        yield path

    def _select_batch(self):
        """Lexical round-robin selection using O(max_files_per_pass) RAM.

        Heap selection still examines each directory entry (O(N log K) CPU),
        but cannot materialize an unbounded path list on a small Pi. On wrap,
        a second streaming pass fills the remaining slots from the beginning.
        """
        row = self.db.execute(
            "SELECT value FROM pipeline_meta WHERE key='cursor_path'"
        ).fetchone()
        cursor = row[0] if row and isinstance(row[0], str) else ""
        limit = self.settings.max_files_per_pass
        selected = heapq.nsmallest(
            limit, (p for p in self._candidates() if str(p) > cursor),
            key=str,
        )
        if cursor and len(selected) < limit:
            selected.extend(heapq.nsmallest(
                limit - len(selected),
                (p for p in self._candidates() if str(p) <= cursor),
                key=str,
            ))
        return selected

    def scan(self):
        lock = self.settings.database.with_suffix(".lock")
        with lock.open("a+b") as handle:
            os.fchmod(handle.fileno(), 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            if free_bytes(self.settings.output_dir) < self.settings.min_free_bytes:
                return self._completed_scan(0, {"paused_low_storage": 1})
            selected = self._select_batch()
            if not selected:
                return self._completed_scan(0, {})
            outcome = {}
            for path in selected:
                state = self.ingest_file(path)
                outcome[state] = outcome.get(state, 0) + 1
            return self._completed_scan(
                len(selected), outcome, cursor_path=str(selected[-1]),
            )


def read_summary(database=None):
    """Read-only stats for Augur.status()/the existing web JSON endpoint."""
    db = Path(database or os.environ.get(
        "REDUX_CAPTURE_DB", "/captures/jobs.db"))
    # Do not follow a replaced ledger symlink from a writable field partition.
    # A missing ledger remains UNKNOWN until the first worker creates it.
    if db.is_symlink():
        return {"available": False, "reason": "capture ledger symlink refused"}
    if not db.is_file():
        return {"available": False, "reason": "capture database not present"}
    try:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro",
                                     timeout=1, uri=True)) as conn:
            grouped = dict(conn.execute(
                "SELECT status, COUNT(*) FROM artifacts GROUP BY status"))
            count = conn.execute(
                "SELECT COALESCE(SUM(hashes),0) FROM artifacts WHERE status='ready'"
            ).fetchone()[0]
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_runs'"
            ).fetchone()
            audits = (dict(conn.execute(
                "SELECT status,COUNT(*) FROM audit_runs GROUP BY status"))
                if present else {})
            scan = None
            scan_error = ""
            row = conn.execute(
                "SELECT value FROM pipeline_meta WHERE key='last_scan'"
            ).fetchone()
            if row is not None:
                try:
                    raw = row[0]
                    if not isinstance(raw, str) or len(raw) > 4096:
                        raise ValueError("heartbeat exceeds permitted length")
                    scan = json.loads(raw)
                    if (not isinstance(scan, dict)
                            or type(scan.get("schema")) is not int
                            or scan["schema"] != 1
                            or type(scan.get("completed_utc")) not in (int, float)
                            or type(scan.get("scanned")) is not int
                            or scan["scanned"] < 0
                            or not isinstance(scan.get("outcomes"), dict)
                            or any(not isinstance(k, str) or len(k) > 80
                                   or type(v) is not int or v < 0
                                   for k, v in scan["outcomes"].items())):
                        raise ValueError("invalid scan record")
                    scan = {
                        "completed_utc": scan["completed_utc"],
                        "scanned": scan["scanned"],
                        "outcomes": scan["outcomes"],
                    }
                except (TypeError, ValueError, json.JSONDecodeError):
                    scan = None
                    scan_error = "invalid stored scan record"
            return {"available": True, "artifacts": sum(grouped.values()),
                    "by_status": grouped, "hash_records": count,
                    "audits": audits, "last_scan": scan,
                    "scan_error": scan_error}
    except (OSError, sqlite3.Error) as error:
        return {"available": False,
                "reason": f"database unavailable: {type(error).__name__}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Redux capture ingestion worker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=20)
    args = parser.parse_args(argv)
    if args.interval < 1:
        parser.error("--interval must be at least one second")
    settings = Settings.load(args.config)
    with CaptureIngestor(settings) as worker:
        while True:
            print(json.dumps(worker.scan(), sort_keys=True), flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
