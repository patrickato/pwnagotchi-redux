"""Optional Pi-local WPA audit worker for already prepared Redux artifacts.

No radio operations. Disabled unless configured, and checks *every* record's
access point against the active Redux Scope before invoking Hashcat.
Never publishes recovered material through status, log, or web endpoints.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import tomllib

from .ingest import _digest_file, normalize_22000


@dataclass(frozen=True)
class AuditSettings:
    enabled: bool
    wordlist: Path
    scope_file: Path
    results_dir: Path
    database: Path
    binary: str = "hashcat"
    max_runtime_seconds: int = 600

    @classmethod
    def load(cls, path):
        with open(path, "rb") as handle:
            config = tomllib.load(handle)
        opts, pipeline = config.get("audit", {}), config.get("pipeline", {})
        return cls(
            enabled=opts.get("enabled") is True,
            wordlist=Path(opts.get("wordlist") or "/nonexistent/redux-wordlist"),
            scope_file=Path(opts.get("scope_file") or "/etc/pwnagotchi/scope.json"),
            results_dir=Path(opts.get("results_dir") or "/captures/audits"),
            database=Path(pipeline.get("database") or "/captures/jobs.db"),
            binary=str(opts.get("binary") or "hashcat"),
            max_runtime_seconds=int(opts.get("max_runtime_seconds") or 600),
        )

    def validate(self):
        paths = (self.wordlist, self.scope_file, self.results_dir, self.database)
        if not all(p.is_absolute() for p in paths):
            raise ValueError("audit paths must be absolute")
        if not 30 <= self.max_runtime_seconds <= 3600:
            raise ValueError("audit maximum runtime must be 30-3600 seconds")


class AuditWorker:
    """Process at most one eligible pending prepared artifact per invocation."""

    def __init__(self, config: AuditSettings, scope, *, runner=None, which=shutil.which):
        config.validate()
        self.cfg = config
        self.scope = scope
        self.runner = runner or subprocess.run
        self.which = which
        self.conn = None

    def __enter__(self):
        if self.cfg.database.exists():
            self.conn = sqlite3.connect(self.cfg.database, timeout=10)
            self.conn.execute("PRAGMA busy_timeout=10000")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS audit_runs (
                output_sha TEXT NOT NULL, wordlist_id TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT NOT NULL,
                result_path TEXT, updated_at REAL NOT NULL,
                PRIMARY KEY (output_sha,wordlist_id)
            )""")
            self.conn.commit()
        return self

    def __exit__(self, *_):
        if self.conn is not None:
            self.conn.close()

    def _record(self, sha, wordlist_id, status, reason, result_path=None):
        self.conn.execute("""INSERT OR REPLACE INTO audit_runs
            (output_sha,wordlist_id,status,reason,result_path,updated_at)
            VALUES(?,?,?,?,?,?)""",
            (sha, wordlist_id, status, reason[:200],
             str(result_path) if result_path else None, time.time()))
        self.conn.commit()

    def once(self):
        if not self.cfg.enabled:
            return {"status": "disabled", "reason": "audit is disabled"}
        if self.conn is None:
            return {"status": "unavailable", "reason": "ingestion database missing"}
        if self.cfg.wordlist.is_symlink() or not self.cfg.wordlist.is_file():
            return {"status": "unavailable", "reason": "wordlist unavailable"}
        if not self.cfg.scope_file.is_file():
            return {"status": "unavailable", "reason": "Redux Scope file unavailable"}
        if self.which(self.cfg.binary) is None:
            return {"status": "unavailable", "reason": "Hashcat not installed"}

        st = self.cfg.wordlist.stat()
        wordlist_id = hashlib.sha256(
            f"{self.cfg.wordlist}:{st.st_size}:{st.st_mtime_ns}".encode()
        ).hexdigest()
        jobs = self.conn.execute("""SELECT DISTINCT output_sha,output_path
            FROM artifacts WHERE status='ready' ORDER BY created_at""").fetchall()

        for output_sha, output_path in jobs:
            prior = self.conn.execute("""SELECT status FROM audit_runs
                WHERE output_sha=? AND wordlist_id=?""",
                (output_sha, wordlist_id)).fetchone()
            if prior and prior[0] in ("recovered", "exhausted", "completed"):
                continue
            artifact = Path(output_path)
            if (not artifact.is_file() or artifact.is_symlink()
                    or artifact.name != output_sha + ".hc22000"
                    or _digest_file(artifact) != output_sha):
                self._record(output_sha, wordlist_id, "error",
                             "prepared artifact missing or changed")
                continue
            records, bssids = normalize_22000(artifact.read_bytes())
            if not records or not bssids:
                self._record(output_sha, wordlist_id, "error",
                             "no valid AP identifiers in artifact")
                continue
            if not all(
                self.scope.permits(
                    bssid=":".join(b[j:j + 2] for j in range(0, 12, 2))
                ) for b in bssids
            ):
                self._record(output_sha, wordlist_id, "skipped",
                             "not all APs are in the configured Redux Scope")
                continue

            self.cfg.results_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.cfg.results_dir, 0o700)
            result_path = self.cfg.results_dir / (
                output_sha + "-" + wordlist_id[:12] + ".found"
            )
            if result_path.is_symlink():
                self._record(output_sha, wordlist_id, "error",
                             "result path is a symbolic link")
                continue
            argv = [
                self.cfg.binary, "-m", "22000", "-a", "0", str(artifact),
                str(self.cfg.wordlist), "--potfile-disable", "--outfile",
                str(result_path), "--outfile-format", "2",
                "--runtime", str(self.cfg.max_runtime_seconds),
                "--session", "redux-" + output_sha[:12] + "-" + wordlist_id[:8],
                "--quiet",
            ]
            try:
                old_mask = os.umask(0o077)
                try:
                    proc = self.runner(
                        argv, capture_output=True,
                        timeout=self.cfg.max_runtime_seconds + 30,
                        check=False,
                    )
                finally:
                    os.umask(old_mask)
                if result_path.exists():
                    os.chmod(result_path, 0o600)
                if result_path.is_file() and result_path.stat().st_size:
                    status, message = ("recovered", "recovery result stored privately")
                elif proc.returncode == 1:
                    status, message = ("exhausted", "wordlist exhausted")
                elif proc.returncode == 0:
                    status, message = ("completed", "worker completed without a match")
                else:
                    status, message = ("error", f"Hashcat exited {proc.returncode}")
            except subprocess.TimeoutExpired:
                status, message = "error", "Hashcat runtime exceeded"
            except (OSError, subprocess.SubprocessError) as error:
                status, message = ("error", "Hashcat execution failed: "
                                   + type(error).__name__)
            self._record(output_sha, wordlist_id, status, message,
                         result_path if status == "recovered" else None)
            return {"status": status, "artifact_id": output_sha[:12],
                    "reason": message}

        return {"status": "idle", "reason": "no new eligible audit artifacts"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in Redux audit worker")
    parser.add_argument("--config", required=True)
    opts = parser.parse_args(argv)
    settings = AuditSettings.load(opts.config)
    if not settings.enabled:
        print(json.dumps({"status": "disabled"}))
        return 0
    from ..core.scope import Scope
    scope = Scope.load(str(settings.scope_file))
    with AuditWorker(settings, scope) as worker:
        print(json.dumps(worker.once(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
