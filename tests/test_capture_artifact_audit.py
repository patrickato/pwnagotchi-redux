"""Offline integrity tests; never access the Pi or real captures."""
import hashlib
import json
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

def test_artifact_size_ceiling_matches_ingestor():
    from redux.crack.ingest import _MAX_PREPARED_BYTES
    assert mod.MAX_ARTIFACT == _MAX_PREPARED_BYTES


def test_oversized_prepared_artifact_is_flagged_without_hashing(tmp_path):
    db, output = fixture(tmp_path)
    path = ready(db, output)
    with path.open("r+b") as stream:
        stream.truncate(mod.MAX_ARTIFACT + 1)
    assert mod.audit(db, output)["issues"] == {"artifact_size_invalid": 1}


def test_canonical_path_is_required_even_with_valid_digest(tmp_path):
    db, output = fixture(tmp_path)
    path = ready(db, output)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE artifacts SET output_path=?", (str(tmp_path / "unrelated"),))
    assert mod.audit(db, output)["issues"] == {"unexpected_artifact_path": 1}
    assert path.exists()


def test_byte_budget_prevents_unbounded_hashing(tmp_path, monkeypatch):
    db, output = fixture(tmp_path)
    ready(db, output, b"artifact bigger than test budget")
    monkeypatch.setattr(mod, "MAX_TOTAL_BYTES", 4)
    report = mod.audit(db, output)
    assert report["result"] == "attention"
    assert report["issues"] == {"audit_byte_budget_exceeded": 1}


def test_default_byte_budget_is_finite():
    assert 0 < mod.MAX_ARTIFACT <= mod.MAX_TOTAL_BYTES <= 512 * 1024 * 1024


def test_explicit_receipt_cli_publishes_verified_cache(tmp_path, capsys):
    from redux.crack.integrity_receipt import read_cached_receipt
    db, output = fixture(tmp_path)
    ready(db, output)
    parent = tmp_path / "diagnostics"
    parent.mkdir()
    dest = parent / "artifact-integrity.json"
    code = mod.main(["--db", str(db), "--output", str(output),
                     "--receipt", str(dest)])
    assert code == 0
    assert read_cached_receipt(dest)["status"] == "ok"
    assert '"schema": 1' in capsys.readouterr().out
    assert dest.stat().st_mode & 0o077 == 0


def test_explicit_receipt_cli_never_follows_symlink(tmp_path, capsys):
    db, output = fixture(tmp_path)
    ready(db, output)
    outside = tmp_path / "outside"
    outside.write_bytes(b"DO NOT OVERWRITE")
    dest = tmp_path / "receipt"
    dest.symlink_to(outside)
    assert mod.main(["--db", str(db), "--output", str(output),
                     "--receipt", str(dest)]) == 2
    assert outside.read_bytes() == b"DO NOT OVERWRITE"
    assert "receipt_publication_failed" in capsys.readouterr().out


def test_cli_refuses_capture_root_without_verified_mount(monkeypatch, capsys):
    from redux import storage_mount
    monkeypatch.setattr(storage_mount, "capture_mount_snapshot", lambda: None)
    monkeypatch.setattr(mod, "audit", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("audit should never read captures without the real ext4 mount")))
    result = mod.main(["--db", "/captures/jobs.db", "--output", "/captures/ready",
                       "--receipt", "/captures/diagnostics/artifact-integrity.json"])
    assert result == 2
    assert '"reason": "capture_mount_unverified"' in capsys.readouterr().out


def test_cli_does_not_publish_after_mount_identity_changes(monkeypatch, capsys):
    from redux import storage_mount
    evidence = iter([(42, "8:1", "ext4", 2, 3),
                     (43, "8:1", "ext4", 2, 3)])
    monkeypatch.setattr(storage_mount, "capture_mount_snapshot", lambda: next(evidence))
    monkeypatch.setattr(mod, "audit", lambda *a, **k:
                        {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}})
    assert mod.main(["--db", "/captures/jobs.db", "--output", "/captures/ready",
                     "--receipt", "/captures/diagnostics/artifact-integrity.json"]) == 2
    assert '"reason": "capture_mount_changed"' in capsys.readouterr().out


def test_artifact_directory_swap_cannot_redirect_hash_to_symlink(tmp_path, monkeypatch):
    """A parent-path swap after directory open must not read the replacement."""
    import os
    db, output = fixture(tmp_path)
    original = ready(db, output, b"legitimate synthetic artifact")
    digest_name = original.name
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / digest_name).write_bytes(b"outside data should never be read")
    saved = tmp_path / "pinned-original"
    original_open = os.open
    changed = [False]

    def swap_before_artifact_open(path, flags, *args, **kwargs):
        if str(path) == digest_name and not changed[0]:
            changed[0] = True
            output.rename(saved)
            output.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(mod.os, "open", swap_before_artifact_open)
    result = mod.audit(db, output)
    assert changed[0]
    # Directory pinning protects what was read, but the advertised name
    # disappeared; that evidence must not become a healthy PASS receipt.
    assert result["result"] == "attention"
    assert result["issues"] == {"output_directory_changed": 1}
    assert result["checked"] == 1
    assert (saved / digest_name).read_bytes() == b"legitimate synthetic artifact"


def test_audit_never_follows_artifact_directory_symlink(tmp_path):
    db, output = fixture(tmp_path)
    ready(db, output)
    link = tmp_path / "prepared-link"
    link.symlink_to(output, target_is_directory=True)
    assert mod.audit(db, link)["issues"] == {"output_directory_untrusted": 1}


def test_wal_writer_cannot_split_ready_count_from_rows(tmp_path, monkeypatch):
    """An active ingest writer changing status must not make a false empty PASS."""
    db, output = fixture(tmp_path)
    ready(db, output)
    with sqlite3.connect(db) as ledger:
        assert ledger.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"

    actual_connect = sqlite3.connect
    changed = [False]

    class ObserveCount:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, statement, *args):
            result = self.conn.execute(statement, *args)
            if statement.startswith("SELECT COUNT(*) FROM artifacts"):
                with actual_connect(db, timeout=1.0) as writer:
                    writer.execute("UPDATE artifacts SET status='error'")
                changed[0] = True
            return result

        def close(self):
            self.conn.close()

    def connect(database, *args, **kwargs):
        # sqlite3.connect(..., uri=True) passes 'uri' as a keyword: using
        # 'uri' as this wrapper's first parameter binds it twice.
        return ObserveCount(actual_connect(database, *args, **kwargs))

    monkeypatch.setattr(mod.sqlite3, "connect", connect)
    report = mod.audit(db, output)
    assert changed == [True]
    assert report == {"result": "pass", "ready_rows": 1, "checked": 1, "issues": {}}
    with actual_connect(db) as ledger:
        assert ledger.execute("SELECT status FROM artifacts").fetchone()[0] == "error"


def test_readonly_transaction_refuses_inconsistent_count(tmp_path, monkeypatch):
    db, output = fixture(tmp_path)
    ready(db, output)
    actual_connect = sqlite3.connect

    class InconsistentSnapshot:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, statement, *args):
            cursor = self.conn.execute(statement, *args)
            if statement.startswith("SELECT output_sha,output_path"):
                class MissingRows:
                    def fetchall(self):
                        return []
                return MissingRows()
            return cursor

        def close(self):
            self.conn.close()

    monkeypatch.setattr(mod.sqlite3, "connect",
                        lambda *a, **kw: InconsistentSnapshot(actual_connect(*a, **kw)))
    report = mod.audit(db, output)
    assert report["result"] == "attention"
    assert report["issues"] == {"audit_unavailable": 1}


def test_artefact_change_with_restored_mtime_is_detected(tmp_path, monkeypatch):
    """Reject a concurrent replacement even if its mtime is restored."""
    import os
    import time

    db, output = fixture(tmp_path)
    path = ready(db, output, b"original synthetic artifact")
    original_read = os.read
    changed = [False]

    def change_after_read(fd, size):
        data = original_read(fd, size)
        if data and not changed[0]:
            changed[0] = True
            before = path.stat()
            # Ensure the ctime changes independently of mtime resolution.
            time.sleep(0.01)
            path.write_bytes(b"modified synthetic artifact")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            assert path.stat().st_mtime_ns == before.st_mtime_ns
            assert path.stat().st_ctime_ns != before.st_ctime_ns
        return data

    monkeypatch.setattr(mod.os, "read", change_after_read)
    report = mod.audit(db, output)
    assert changed == [True]
    assert report["result"] == "attention"
    assert report["issues"] == {"artifact_changed_during_audit": 1}


def test_ledger_replaced_after_snapshot_is_not_pass(tmp_path, monkeypatch):
    """A complete SQLite read cannot certify a subsequently replaced jobs.db."""
    import os
    db, output = fixture(tmp_path)
    original = ready(db, output)
    replacement = tmp_path / "unrelated.db"
    with sqlite3.connect(replacement) as conn:
        conn.execute("CREATE TABLE artifacts (source_sha TEXT, status TEXT, output_sha TEXT, output_path TEXT)")
    saved = tmp_path / "original-jobs.db"
    actual_open = os.open
    swapped = [False]

    def replacing_open(path, flags, *args, **kwargs):
        if path == original.name and kwargs.get("dir_fd") is not None and not swapped[0]:
            swapped[0] = True
            db.rename(saved)
            db.symlink_to(replacement)
        return actual_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(mod.os, "open", replacing_open)
    result = mod.audit(db, output)
    assert swapped == [True]
    assert result["checked"] == 1
    assert result["issues"] == {"ledger_changed": 1}
    assert result["result"] == "attention"
    assert saved.is_file()


def test_wal_writer_same_ledger_inode_is_allowed(tmp_path):
    """Committed SQLite WAL updates may change data but not ledger identity."""
    db, output = fixture(tmp_path)
    ready(db, output)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    assert mod.audit(db, output)["result"] == "pass"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE artifacts SET status='error'")
    assert mod.audit(db, output)["result"] == "unknown"


def test_cached_inspection_reports_verified_without_running_audit(tmp_path, capsys, monkeypatch):
    """This command must only read bounded receipt metadata."""
    from redux.crack.integrity_receipt import publish_receipt
    dest = tmp_path / "integrity.json"
    publish_receipt(dest, {
        "result": "pass", "checked": 1, "ready_rows": 1, "issues": {},
    })
    monkeypatch.setattr(mod, "audit", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("inspection must not read ledger or hash artifacts")))
    assert mod.main(["--inspect-receipt", str(dest)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "ok"
    assert response["checked"] == 1
    assert set(response) == {
        "status", "reason", "checked", "ready_rows", "age_seconds"}


def test_cached_inspection_reports_action_and_missing_as_distinct(tmp_path, capsys):
    from redux.crack.integrity_receipt import publish_receipt
    path = tmp_path / "integrity.json"
    assert mod.main(["--inspect-receipt", str(path)]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "unknown"
    publish_receipt(path, {
        "result": "attention", "checked": 1, "ready_rows": 1,
        "issues": {"artifact_missing": 1},
    })
    assert mod.main(["--inspect-receipt", str(path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "attention"
    assert result["reason"] == "integrity_discrepancies"


def test_cached_inspection_capture_mount_unavailable_fails_closed(monkeypatch, capsys):
    from redux import storage_mount
    from redux.crack import integrity_receipt
    monkeypatch.setattr(storage_mount, "capture_mount_snapshot", lambda: None)
    monkeypatch.setattr(integrity_receipt, "read_cached_receipt",
                        lambda path: (_ for _ in ()).throw(
                            AssertionError("unmounted cache must not be opened")))
    assert mod.main(["--inspect-receipt",
                     "/captures/diagnostics/artifact-integrity.json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unknown"
    assert result["reason"] == "capture_mount_unverified"


def test_cached_inspection_mount_swap_cannot_return_ok(monkeypatch, capsys):
    from redux import storage_mount
    from redux.crack import integrity_receipt
    stamps = iter([(1, "8:1", "ext4", 2, 3),
                   (2, "8:1", "ext4", 2, 3)])
    monkeypatch.setattr(storage_mount, "capture_mount_snapshot",
                        lambda: next(stamps))
    monkeypatch.setattr(integrity_receipt, "read_cached_receipt",
                        lambda path: {"status": "ok", "reason": "verified_ready_artifacts",
                                      "checked": 1, "ready_rows": 1,
                                      "age_seconds": 0})
    assert mod.main(["--inspect-receipt",
                     "/captures/diagnostics/artifact-integrity.json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unknown"
    assert result["reason"] == "capture_mount_changed"


def test_cached_inspection_and_audit_publication_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit) as err:
        mod.main(["--inspect-receipt", str(tmp_path / "a"),
                  "--receipt", str(tmp_path / "b")])
    assert err.value.code == 2


def test_prepared_filename_replacement_is_not_a_false_pass(tmp_path, monkeypatch):
    """The old open inode can hash correctly while its name points elsewhere."""
    import os

    db, output = fixture(tmp_path)
    payload = b"stable synthetic prepared artifact"
    path = ready(db, output, payload)
    old_inode = path.stat().st_ino
    original_read = os.read
    swapped = [False]

    def replace_name_during_read(fd, count):
        chunk = original_read(fd, count)
        if chunk and not swapped[0]:
            swapped[0] = True
            path.unlink()
            # Even identical content and digest must not hide inode churn.
            path.write_bytes(payload)
            assert path.stat().st_ino != old_inode
        return chunk

    monkeypatch.setattr(mod.os, "read", replace_name_during_read)
    observed = mod.audit(db, output)
    assert swapped == [True]
    assert observed["checked"] == 1
    assert observed["issues"] == {"artifact_changed_during_audit": 1}
    assert observed["result"] == "attention"


def test_prepared_filename_swap_to_symlink_is_not_a_pass(tmp_path, monkeypatch):
    """Only the pinned original inode may be read, not replacement symlink data."""
    import os

    db, output = fixture(tmp_path)
    payload = b"synthetic private capture conversion"
    path = ready(db, output, payload)
    outside = tmp_path / "different-tree"
    outside.write_bytes(payload)
    original_read = os.read
    changed = [False]

    def replace_name_with_link(fd, count):
        chunk = original_read(fd, count)
        if chunk and not changed[0]:
            changed[0] = True
            path.unlink()
            path.symlink_to(outside)
        return chunk

    monkeypatch.setattr(mod.os, "read", replace_name_with_link)
    observed = mod.audit(db, output)
    assert changed == [True]
    assert observed["result"] == "attention"
    assert observed["issues"] == {"artifact_changed_during_audit": 1}
    assert outside.read_bytes() == payload
