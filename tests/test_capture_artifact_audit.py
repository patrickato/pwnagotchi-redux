"""Offline integrity tests; never access the Pi or real captures."""
import hashlib
import importlib.util
from pathlib import Path
import sqlite3

import pytest

SRC = Path(__file__).resolve().parents[1] / "scripts" / "capture-artifact-audit.py"
spec = importlib.util.spec_from_file_location("capture_artifact_audit", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture(tmp_path):
    output = tmp_path / "ready"
    output.mkdir()
    db = tmp_path / "jobs.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE artifacts (source_sha TEXT, status TEXT, output_sha TEXT, output_path TEXT)")
    return db, output


def ready(db, output, data=b"synthetic prepared record"):
    digest = hashlib.sha256(data).hexdigest()
    path = output / (digest + ".hc22000")
    path.write_bytes(data)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artifacts VALUES (?,?,?,?)", ("a" * 64, "ready", digest, str(path)))
    return path


def test_matching_prepared_artifact(tmp_path):
    db, output = fixture(tmp_path)
    ready(db, output)
    report = mod.audit(db, output)
    assert report == {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}}


def test_missing_and_changed_prepared_artifact(tmp_path):
    db, output = fixture(tmp_path)
    path = ready(db, output)
    path.unlink()
    assert mod.audit(db, output)["issues"] == {"artifact_missing": 1}
    path.write_bytes(b"tampered")
    assert mod.audit(db, output)["issues"] == {"artifact_digest_mismatch": 1}


def test_symlink_cannot_read_outside_directory(tmp_path):
    db, output = fixture(tmp_path)
    path = ready(db, output)
    outside = tmp_path / "private"
    outside.write_bytes(b"SECRET")
    path.unlink()
    path.symlink_to(outside)
    assert mod.audit(db, output)["issues"] == {"artifact_unreadable": 1}
    assert outside.read_bytes() == b"SECRET"


def test_untrusted_ledger_and_directory(tmp_path):
    db, output = fixture(tmp_path)
    link = tmp_path / "ledger-link"
    link.symlink_to(db)
    assert mod.audit(link, output)["issues"] == {"ledger_unavailable": 1}
    outlink = tmp_path / "dir-link"
    outlink.symlink_to(output)
    assert mod.audit(db, outlink)["issues"] == {"output_directory_untrusted": 1}


def test_invalid_digest_and_noncanonical_path(tmp_path):
    db, output = fixture(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artifacts VALUES (?,?,?,?)", ("a", "ready", "invalid", str(output / "invalid")))
    assert mod.audit(db, output)["issues"] == {"invalid_digest": 1}


def test_rows_are_bounded_and_no_capture_identifiers_leak(tmp_path):
    db, output = fixture(tmp_path)
    ready(db, output)
    assert mod.audit(db, output, max_rows=0)["issues"] == {"row_limit_exceeded": 1}
    report = str(mod.audit(db, output))
    assert str(output) not in report
    assert "synthetic" not in report


def test_missing_schema(tmp_path):
    output = tmp_path / "ready"
    output.mkdir()
    db = tmp_path / "jobs.db"
    sqlite3.connect(db).close()
    assert mod.audit(db, output)["issues"] == {"schema_missing": 1}

def test_empty_ledger_is_unknown_not_verified(tmp_path):
    db, output = fixture(tmp_path)
    report = mod.audit(db, output)
    assert report == {"result": "unknown", "checked": 0, "ready_rows": 0, "issues": {}}


def test_empty_ledger_cli_returns_unknown_exit_code(tmp_path, capsys):
    db, output = fixture(tmp_path)
    assert mod.main(["--db", str(db), "--output", str(output)]) == 3
    assert '"result": "unknown"' in capsys.readouterr().out
