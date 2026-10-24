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
from ..core.scope import default_scope_path as _default_scope_path


@dataclass(frozen=True)
class AuditSettings:
    enabled: bool
    wordlist: Path
    scope_file: Path
    results_dir: Path
    database: Path
    binary: str = "hashcat"
    max_runtime_seconds: int = 1800
    # Registry used only for the lab-mode turnkey fallback below. When lab mode is
    # on and the configured wordlist isn't usable, the worker resolves the fastest
    # list registered here so the crack runs without any extra config.
    wordlist_dir: Path = Path("/captures/wordlists")

    @classmethod
    def load(cls, path):
        with open(path, "rb") as handle:
            config = tomllib.load(handle)
        opts, pipeline = config.get("audit", {}), config.get("pipeline", {})
        # Wordlist resolution: if a registry dir + a name/tier are configured, pick
        # the list through the glass-box WordlistStore; otherwise keep the single
        # literal `wordlist` path (fully backward-compatible).
        wordlist = opts.get("wordlist") or "/nonexistent/redux-wordlist"
        wl_dir = opts.get("wordlist_dir")
        if wl_dir and (opts.get("wordlist_name") or opts.get("wordlist_tier")):
            from .wordlists import resolve_wordlist
            resolved, _reason = resolve_wordlist(
                wl_dir, name=opts.get("wordlist_name"), tier=opts.get("wordlist_tier"))
            if resolved:
                wordlist = resolved
        return cls(
            enabled=opts.get("enabled") is True,
            wordlist=Path(wordlist),
            scope_file=Path(opts.get("scope_file") or _default_scope_path()),
            results_dir=Path(opts.get("results_dir") or "/captures/audits"),
            database=Path(pipeline.get("database") or "/captures/jobs.db"),
            binary=str(opts.get("binary") or "hashcat"),
            max_runtime_seconds=int(opts.get("max_runtime_seconds") or 600),
            wordlist_dir=Path(wl_dir or "/captures/wordlists"),
        )

    def validate(self):
        paths = (self.wordlist, self.scope_file, self.results_dir,
                 self.database, self.wordlist_dir)
        if not all(p.is_absolute() for p in paths):
            raise ValueError("audit paths must be absolute")
        # Up to 24h per capture: it's the owner's hardware cracking the owner's
        # own handshake, so an overnight run is a legitimate choice, not a footgun.
        # The lower bound and the +30s wall-clock timeout wrapper stay.
        if not 30 <= self.max_runtime_seconds <= 86400:
            raise ValueError("audit maximum runtime must be 30-86400 seconds")


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
        # Lab mode makes the crack part of the automatic chain: the worker runs
        # even if the config opt-in is off. Every other mode stays opt-in.
        lab = bool(getattr(self.scope, "lab_mode", False))
        if not self.cfg.enabled and not lab:
            return {"status": "disabled",
                    "reason": "audit is disabled (enable it in config, or turn on lab mode)"}
        if self.conn is None:
            return {"status": "unavailable", "reason": "ingestion database missing"}
        # Turnkey wordlist for lab mode: if the configured list isn't usable, fall
        # back to the fastest list in the on-device registry so a freshly-armed lab
        # box cracks without extra config. Other modes keep the configured list.
        wordlist = self.cfg.wordlist
        if (wordlist.is_symlink() or not wordlist.is_file()) and lab:
            from .wordlists import resolve_wordlist
            resolved, _reason = resolve_wordlist(str(self.cfg.wordlist_dir))
            if resolved:
                wordlist = Path(resolved)
        if wordlist.is_symlink() or not wordlist.is_file():
            return {"status": "unavailable", "reason": "wordlist unavailable"}
        if not self.cfg.scope_file.is_file():
            return {"status": "unavailable", "reason": "Redux Scope file unavailable"}
        if self.which(self.cfg.binary) is None:
            return {"status": "unavailable", "reason": "Hashcat not installed"}

        st = wordlist.stat()
        wordlist_id = hashlib.sha256(
            f"{wordlist}:{st.st_size}:{st.st_mtime_ns}".encode()
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
                str(wordlist), "--potfile-disable", "--outfile",
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
    parser.add_argument("--max-jobs", type=int, default=8,
                        help="drain up to N eligible artifacts this run so a backlog "
                             "doesn't trickle out one-per-timer (1 = a single job)")
    opts = parser.parse_args(argv)
    settings = AuditSettings.load(opts.config)
    from ..core.scope import Scope
    scope = Scope.load(str(settings.scope_file))
    # Lab mode runs the crack automatically; every other mode is config opt-in.
    if not settings.enabled and not bool(getattr(scope, "lab_mode", False)):
        print(json.dumps({"status": "disabled"}))
        return 0
    terminal = {"idle", "disabled", "unavailable"}
    with AuditWorker(settings, scope) as worker:
        runs = []
        for _ in range(max(1, opts.max_jobs)):
            result = worker.once()
            runs.append(result)
            if result.get("status") in terminal:
                break
        # Keep the single-run output shape when only one run happened (backward
        # compatible); summarise when we actually drained several.
        if len(runs) > 1:
            processed = [r for r in runs if r.get("status") not in terminal]
            print(json.dumps({"status": "batch", "processed": len(processed),
                              "runs": runs}, sort_keys=True))
        else:
            print(json.dumps(runs[0], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
