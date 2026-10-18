"""Operator-visible, targeted recovery for interrupted capture processing.

A dry run is strictly read-only. An explicit apply retries source captures
already recorded as errors or whose prepared output has gone missing, without
deleting any raw capture, prepared artifact or ledger record. This is an
operator action, never part of the automatic timer.
"""
from __future__ import annotations

from contextlib import closing
import re
from pathlib import Path
import sqlite3
import stat
import time

from ..storage_mount import capture_mount_snapshot
_OUTPUT_SHA = re.compile(r"^[0-9a-f]{64}$")

from .ingest import (
    CaptureIngestor, CaptureMountLost, Settings, SUFFIXES,
    _digest_file, capture_scan_lock, requires_capture_mount,
)

# Ingestion accepts at most 32 MiB of converter material. A small margin
# permits normalized line endings without allowing unbounded recovery reads.
_MAX_PREPARED_BYTES = 33 * 1024 * 1024


def _snapshot_checked(settings):
    if not requires_capture_mount(settings):
        return None
    current = capture_mount_snapshot()
    if current is None:
        raise CaptureMountLost("REDUXCAP mount absent; capture recovery refused")
    return current


def _recheck_mount(settings, snapshot):
    if requires_capture_mount(settings) and (
            snapshot is None or capture_mount_snapshot() != snapshot):
        raise CaptureMountLost(
            "REDUXCAP mount missing or changed; capture recovery refused"
        )


def _candidates(conn, settings, *, max_files, now):
    """Bounded ledger assessment with no capture content in the report."""
    inspected_limit = max_files * 10
    rows = conn.execute(
        """SELECT source_path,status,output_sha,output_path FROM artifacts
           WHERE status IN ('error','ready','duplicate')
           ORDER BY updated_at ASC LIMIT ?""", (inspected_limit + 1,),
    ).fetchall()
    result = {
        "records_checked": 0, "retryable": 0, "recorded_errors": 0,
        "missing_outputs": 0, "altered_outputs": 0,
        "blocked": 0, "deferred": 0,
        "truncated": len(rows) > inspected_limit,
    }
    retry = []
    for raw_source, status, output_sha, raw_output in rows[:inspected_limit]:
        result["records_checked"] += 1
        if len(retry) >= max_files:
            result["truncated"] = True
            break
        if not isinstance(raw_source, str) or not raw_source:
            result["blocked"] += 1
            continue
        source = Path(raw_source)
        # Restrict recovery to the exact configured non-symlinked input
        # directories. Never revisit arbitrary paths from a mutable ledger.
        if (not source.is_absolute() or source.parent not in settings.inputs
                or source.suffix.lower() not in SUFFIXES
                or source.parent.is_symlink() or not source.parent.is_dir()):
            result["blocked"] += 1
            continue
        try:
            st = source.lstat()
        except FileNotFoundError:
            result["deferred"] += 1
            continue
        except OSError:
            result["blocked"] += 1
            continue
        if not stat.S_ISREG(st.st_mode):
            result["blocked"] += 1
            continue
        if (st.st_size == 0 or st.st_size > settings.max_capture_bytes
                or now - st.st_mtime < settings.settle_seconds):
            result["deferred"] += 1
            continue

        if status != "error":
            # A valid processed ledger row with its prepared output still
            # present is not a recovery candidate. Missing output is
            # regenerable by ingest_file, provided its original raw remains.
            if (not isinstance(output_sha, str)
                    or _OUTPUT_SHA.fullmatch(output_sha) is None
                    or not isinstance(raw_output, str)
                    or Path(raw_output) !=
                    settings.output_dir / (output_sha + ".hc22000")):
                result["blocked"] += 1
                continue
            output = Path(raw_output)
            if output.is_symlink():
                result["blocked"] += 1
                continue
            if output.exists():
                if not output.is_file():
                    result["blocked"] += 1
                    continue
                # Report mismatched prepared bytes now, without opening the
                # source capture or overwriting an existing result. Ingestion
                # already refuses collisions; operator recovery must too.
                try:
                    verified_sha = _digest_file(
                        output, max_bytes=_MAX_PREPARED_BYTES)
                except ValueError:
                    # An oversized prepared output cannot be an authentic
                    # product of our bounded conversion process.
                    result["altered_outputs"] += 1
                    result["blocked"] += 1
                    continue
                except OSError:
                    result["blocked"] += 1
                    continue
                if verified_sha != output_sha:
                    result["altered_outputs"] += 1
                    result["blocked"] += 1
                continue
            result["missing_outputs"] += 1
        else:
            result["recorded_errors"] += 1
        retry.append(source)
        result["retryable"] += 1
    return result, retry


def recovery_report(settings: Settings, *, apply=False, max_files=100,
                    clock=time.time, runner=None):
    """Assess or retry bounded failed/missing-output captures.

    Dry-run never creates/chmods a database, lock file or output directory.
    --apply uses the ingestion worker's same exclusive flock and mount checks.
    The report deliberately excludes raw filenames, MACs and capture data.
    """
    if not 1 <= max_files <= 1000:
        raise ValueError("recovery max_files must be between 1 and 1000")
    mount = _snapshot_checked(settings)
    report = {
        "mode": "apply" if apply else "dry_run", "ledger_available": False,
        "records_checked": 0, "retryable": 0, "recorded_errors": 0,
        "missing_outputs": 0, "altered_outputs": 0,
        "blocked": 0, "deferred": 0,
        "truncated": False, "attempted": 0, "outcomes": {},
        "note": "Source captures and prepared artifacts are never deleted.",
    }
    if settings.database.is_symlink():
        raise ValueError("capture recovery refuses symlinked ledger")
    if not settings.database.is_file():
        _recheck_mount(settings, mount)
        return report

    if not apply:
        with closing(sqlite3.connect(
                settings.database.as_uri() + "?mode=ro",
                uri=True, timeout=3,
        )) as db:
            counts, _ = _candidates(
                db, settings, max_files=max_files, now=clock(),
            )
        _recheck_mount(settings, mount)
        report.update(counts)
        report["ledger_available"] = True
        return report

    with CaptureIngestor(settings, runner=runner, clock=clock) as worker:
        # Only the existing worker may mutate capture ledger state; lock it
        # against scheduled ingestion and manual retention for the whole pass.
        with capture_scan_lock(settings.database):
            worker._assert_mount()
            _recheck_mount(settings, mount)
            counts, files = _candidates(
                worker.db, settings, max_files=max_files, now=clock(),
            )
            report.update(counts)
            report["ledger_available"] = True
            for source in files:
                _recheck_mount(settings, mount)
                worker._assert_mount()
                state = worker.ingest_file(source)
                report["attempted"] += 1
                report["outcomes"][state] = report["outcomes"].get(state, 0) + 1
            _recheck_mount(settings, mount)
            worker._assert_mount()
    return report
