"""Storage retention is explicit, verified, bounded, and dry-run by default."""
import os
from pathlib import Path
import subprocess
import time

import pytest

from redux.crack.ingest import CaptureIngestor, Settings
from redux.crack.retention import retention_report

NOW = 1_800_000_000.0
REC = ("WPA*01*" + "00"*16 + "*aabbccddeeff*112233445566*74657374***\n")


def create(tmp_path, *, age_days=50, recorded_days=40, valid=True):
    incoming = tmp_path / "incoming"
    incoming.mkdir(exist_ok=True)
    source = incoming / "first.pcap"
    source.write_bytes(b"synthetic captured frames for controlled lab")
    mtime = NOW - age_days * 86400
    os.utime(source, (mtime, mtime))
    cfg = Settings((incoming,), tmp_path / "ready", tmp_path / "db" / "jobs.db",
                   settle_seconds=0, min_free_bytes=1024*1024)

    def converter(argv, **kwargs):
        Path(argv[2]).write_text(REC if valid else "no usable records\n")
        return subprocess.CompletedProcess(argv, 0)

    with CaptureIngestor(cfg, runner=converter,
                         clock=lambda: NOW - recorded_days * 86400) as ing:
        assert ing.scan()["scanned"] == 1
        rows = ing.rows()
    return cfg, source, rows[0]


def test_dry_run_never_removes_capture_or_converted_artifact(tmp_path):
    cfg, source, row = create(tmp_path)
    original = source.read_bytes()
    report = retention_report(cfg, clock=lambda: NOW)
    assert report["mode"] == "dry_run"
    assert report["eligible"] == 1
    assert report["removed"] == 0
    assert report["reclaimable_bytes"] == len(original)
    assert source.read_bytes() == original
    assert Path(row["output_path"]).is_file()


def test_explicit_apply_reclaims_only_verified_old_raw_file(tmp_path):
    cfg, source, row = create(tmp_path)
    artifact = Path(row["output_path"])
    report = retention_report(cfg, apply=True, clock=lambda: NOW)
    assert report["removed"] == 1
    assert report["reclaimed_bytes"] > 0
    assert not source.exists()
    assert artifact.read_text() == REC
    assert cfg.database.exists()
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0


def test_minimum_age_and_recent_conversion_are_protected(tmp_path):
    cfg, source, _ = create(tmp_path, age_days=50, recorded_days=1)
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0
    assert source.is_file()
    with pytest.raises(ValueError, match="between 7"):
        retention_report(cfg, apply=True, older_than_days=1, clock=lambda: NOW)


def test_recent_source_is_protected_even_if_record_is_old(tmp_path):
    cfg, source, _ = create(tmp_path)
    os.utime(source, (NOW - 86400, NOW - 86400))
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["eligible"] == 0
    assert source.is_file()


def test_failed_conversion_is_never_deleted(tmp_path):
    cfg, source, row = create(tmp_path, valid=False)
    assert row["status"] == "invalid"
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0
    assert source.is_file()


def test_missing_or_corrupted_prepared_file_prevents_deletion(tmp_path):
    cfg, source, row = create(tmp_path)
    converted = Path(row["output_path"])
    converted.unlink()
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0
    assert source.is_file()

    converted.write_bytes(b"corrupted result")
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0
    assert source.is_file()


def test_source_tampering_and_symlink_are_protected(tmp_path):
    cfg, source, row = create(tmp_path)
    source.write_bytes(b"changed without updating ledger")
    os.utime(source, (NOW - 50*86400, NOW - 50*86400))
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0
    source.unlink()
    outside = tmp_path / "private-outside"
    outside.write_bytes(b"not a capture")
    source.symlink_to(outside)
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0
    assert outside.read_bytes() == b"not a capture"


def test_hardlinked_source_is_not_deleted_mid_handoff(tmp_path):
    cfg, source, _ = create(tmp_path)
    second = tmp_path / "active.pcap"
    os.link(source, second)
    assert retention_report(cfg, apply=True, clock=lambda: NOW)["removed"] == 0
    assert source.exists() and second.exists()


def test_missing_database_dry_run_creates_no_files(tmp_path):
    cfg = Settings((tmp_path / "none",), tmp_path / "ready", tmp_path / "db" / "jobs.db")
    report = retention_report(cfg, clock=lambda: NOW)
    assert report["eligible"] == 0
    assert not cfg.database.exists()


def test_bounded_batch_leaves_remaining_sources(tmp_path):
    cfg, source, row = create(tmp_path)
    second = cfg.inputs[0] / "second.pcap"
    second.write_bytes(b"another verified controlled capture")
    os.utime(second, (NOW-50*86400, NOW-50*86400))
    def convert(argv, **kwargs):
        Path(argv[2]).write_text(REC)
        return subprocess.CompletedProcess(argv, 0)
    with CaptureIngestor(cfg, runner=convert, clock=lambda: NOW-40*86400) as worker:
        assert worker.ingest_file(second) in {"ready", "duplicate"}
    first = retention_report(cfg, apply=True, max_files=1, clock=lambda: NOW)
    assert first["removed"] == 1
    assert sum(p.is_file() for p in (source, second)) == 1
