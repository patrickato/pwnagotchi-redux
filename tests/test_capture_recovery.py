"""Targeted recovery of previously recorded capture conversion failures.

All source captures and candidate hashes are synthetic test material; no RF.
"""
import os
from pathlib import Path
import subprocess
import time

import pytest

from redux.crack.ingest import CaptureIngestor, Settings
from redux.crack.recovery import recovery_report

REC = "WPA*01*" + "00" * 16 + "*aabbccddeeff*112233445566*74657374***\n"


def settings(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    return Settings((incoming,), tmp_path / "ready",
                    tmp_path / "state" / "jobs.db", settle_seconds=0)


def fixture_source(path, content=b"synthetic-capture-input"):
    path.write_bytes(content)
    past = time.time() - 120
    os.utime(path, (past, past))
    return path


def fake_success(argv, **kwargs):
    assert argv[-1].startswith("/proc/self/fd/")
    assert kwargs["pass_fds"] == (int(argv[-1].rsplit("/", 1)[1]),)
    Path(argv[2]).write_text(REC)
    return subprocess.CompletedProcess(argv, 0)


def fake_failure(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 2)


def test_recovery_dry_run_does_not_modify_failed_ledger_or_files(tmp_path):
    cfg = settings(tmp_path)
    source = fixture_source(cfg.inputs[0] / "failed.pcap")
    with CaptureIngestor(cfg, runner=fake_failure) as worker:
        assert worker.ingest_file(source) == "error"
        prior_rows = worker.rows()
    modified = cfg.database.stat().st_mtime_ns
    dry = recovery_report(cfg)
    assert dry["mode"] == "dry_run"
    assert dry["ledger_available"] and dry["retryable"] == 1
    assert dry["recorded_errors"] == 1 and dry["missing_outputs"] == 0
    assert dry["attempted"] == 0 and dry["outcomes"] == {}
    assert cfg.database.stat().st_mtime_ns == modified
    assert source.is_file()
    with CaptureIngestor(cfg) as worker:
        assert worker.rows() == prior_rows


def test_operator_retries_failed_conversion_and_preserves_raw_capture(tmp_path):
    cfg = settings(tmp_path)
    source = fixture_source(cfg.inputs[0] / "retry.pcapng")
    original = source.read_bytes()
    with CaptureIngestor(cfg, runner=fake_failure) as worker:
        assert worker.ingest_file(source) == "error"
    report = recovery_report(cfg, apply=True, runner=fake_success)
    assert report["attempted"] == 1
    assert report["outcomes"] == {"ready": 1}
    assert source.read_bytes() == original
    with CaptureIngestor(cfg) as worker:
        assert worker.rows()[0]["status"] == "ready"
        assert worker.summary()["hash_records"] == 1
    assert recovery_report(cfg)["retryable"] == 0


def test_missing_prepared_output_rebuilt_only_on_explicit_apply(tmp_path):
    cfg = settings(tmp_path)
    source = fixture_source(cfg.inputs[0] / "recover.hc22000", REC.encode())
    with CaptureIngestor(cfg) as worker:
        assert worker.ingest_file(source) == "ready"
        artifact = Path(worker.rows()[0]["output_path"])
    artifact.unlink()
    dry = recovery_report(cfg)
    assert dry["missing_outputs"] == dry["retryable"] == 1
    assert not artifact.exists()
    report = recovery_report(cfg, apply=True)
    assert report["outcomes"] == {"ready": 1}
    assert artifact.read_text() == REC
    assert source.is_file()
    assert recovery_report(cfg)["retryable"] == 0


def test_missing_ledger_does_not_create_state_even_on_apply(tmp_path):
    cfg = settings(tmp_path)
    assert not cfg.database.exists()
    assert recovery_report(cfg)["ledger_available"] is False
    assert recovery_report(cfg, apply=True)["ledger_available"] is False
    assert not cfg.output_dir.exists()
    assert not cfg.database.parent.exists()


def test_recovery_never_chases_symlinked_source(tmp_path):
    cfg = settings(tmp_path)
    source = fixture_source(cfg.inputs[0] / "link.pcap")
    private = tmp_path / "outside"
    private.write_bytes(b"must remain untouched")
    with CaptureIngestor(cfg, runner=fake_failure) as worker:
        assert worker.ingest_file(source) == "error"
    source.unlink()
    source.symlink_to(private)
    report = recovery_report(cfg, apply=True, runner=lambda *a, **k: pytest.fail(
        "converter should never be called"))
    assert report["retryable"] == report["attempted"] == 0
    assert report["blocked"] >= 1
    assert private.read_bytes() == b"must remain untouched"


def test_recovery_bounded_to_max_files(tmp_path):
    cfg = settings(tmp_path)
    for i in range(5):
        source = fixture_source(cfg.inputs[0] / f"failed-{i}.pcap", f"raw-{i}".encode())
        with CaptureIngestor(cfg, runner=fake_failure) as worker:
            assert worker.ingest_file(source) == "error"
    dry = recovery_report(cfg, max_files=2)
    assert dry["retryable"] == 2
    assert dry["truncated"] is True
    result = recovery_report(cfg, apply=True, max_files=2, runner=fake_success)
    assert result["attempted"] == 2
    assert sum(result["outcomes"].values()) == 2
    assert set(result["outcomes"]) <= {"ready", "duplicate"}
    assert recovery_report(cfg)["retryable"] == 3
    with pytest.raises(ValueError, match="between 1"):
        recovery_report(cfg, max_files=0)


def test_dry_run_refuses_symlinked_ledger(tmp_path):
    cfg = settings(tmp_path)
    ledger = cfg.database
    ledger.parent.mkdir(parents=True)
    private = tmp_path / "private"
    private.write_bytes(b"secret")
    ledger.symlink_to(private)
    with pytest.raises(ValueError, match="symlinked ledger"):
        recovery_report(cfg)
    assert private.read_bytes() == b"secret"


def test_mount_is_revalidated_before_recovery_apply(tmp_path, monkeypatch):
    import redux.crack.recovery as recovery
    cfg = settings(tmp_path)
    source = fixture_source(cfg.inputs[0] / "failed.pcap")
    with CaptureIngestor(cfg, runner=fake_failure) as worker:
        assert worker.ingest_file(source) == "error"
    previous = [("mount", 10)]
    monkeypatch.setattr(recovery, "requires_capture_mount", lambda _settings: True)
    monkeypatch.setattr(recovery, "capture_mount_snapshot", lambda: previous[0])
    first = recovery.recovery_report(cfg)
    assert first["retryable"] == 1

    def change_at_check():
        old = previous[0]
        previous[0] = ("other", 20)
        return old

    # A substituted mount between inspection and processing cannot count
    # as a verified recovery. Actual Pi hot-unmount remains a field gate.
    monkeypatch.setattr(recovery, "capture_mount_snapshot", change_at_check)
    with pytest.raises(recovery.CaptureMountLost, match="changed"):
        recovery.recovery_report(cfg)
