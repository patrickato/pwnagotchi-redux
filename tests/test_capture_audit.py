from pathlib import Path
import os
import subprocess
import time

from redux.crack.ingest import CaptureIngestor, Settings
from redux.crack.audit import AuditSettings, AuditWorker

MAC1 = "aabbccddeeff"
MAC2 = "112233445566"
REC = f"WPA*01*{'00'*16}*{MAC1}*{MAC2}*74657374***"
REC_OTHER = f"WPA*01*{'11'*16}*112233445577*{MAC2}*74657374***"


class Scope:
    def __init__(self, permitted=()):
        self.permitted = permitted

    def permits(self, *, bssid=None):
        return bssid in self.permitted


def make(tmp_path, lines=(REC,)):
    inp = tmp_path / "in"
    inp.mkdir()
    cfg = Settings((inp,), tmp_path / "out", tmp_path / "db" / "jobs.db",
                   settle_seconds=0)
    capture = inp / "lab.hc22000"
    capture.write_text("\n".join(lines) + "\n")
    os.utime(capture, (time.time()-10, time.time()-10))
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(capture) == "ready"
    wordlist = tmp_path / "words.txt"
    wordlist.write_text("sample\n")
    scopefile = tmp_path / "scope.json"
    scopefile.write_text("{}")
    return AuditSettings(True, wordlist, scopefile, tmp_path / "audits", cfg.database)


def test_disabled(tmp_path):
    c = make(tmp_path)
    c = AuditSettings(False, c.wordlist, c.scope_file, c.results_dir, c.database)
    with AuditWorker(c, Scope(), which=lambda x: "hashcat") as worker:
        assert worker.once()["status"] == "disabled"


def test_unlisted_target_never_runs_hashcat(tmp_path):
    c = make(tmp_path)
    calls = []
    def runner(*args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess([], 0)
    with AuditWorker(c, Scope(), runner=runner, which=lambda x: "hashcat") as worker:
        assert worker.once()["status"] == "idle"
    assert not calls


def test_approved_scope_runs_once(tmp_path):
    c = make(tmp_path)
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1)
    with AuditWorker(c, Scope(["aa:bb:cc:dd:ee:ff"]),
                     runner=runner, which=lambda x: "hashcat") as worker:
        assert worker.once()["status"] == "exhausted"
        assert worker.once()["status"] == "idle"
    assert len(calls) == 1
    assert calls[0][0] == "hashcat"
    assert calls[0][calls[0].index("-m") + 1] == "22000"
    assert "--potfile-disable" in calls[0]


def test_all_bssids_must_match_scope(tmp_path):
    c = make(tmp_path, (REC, REC_OTHER))
    with AuditWorker(c, Scope(["aa:bb:cc:dd:ee:ff"]),
                     runner=lambda *x, **kw: (_ for _ in ()).throw(
                         RuntimeError("unexpected launch")),
                     which=lambda x: "hashcat") as worker:
        assert worker.once()["status"] == "idle"


def test_private_result_without_web_or_log_leak(tmp_path):
    c = make(tmp_path)
    secret = "synthetic-secret-value"
    def runner(cmd, **kw):
        Path(cmd[cmd.index("--outfile") + 1]).write_text(secret + "\n")
        return subprocess.CompletedProcess(cmd, 0)
    with AuditWorker(c, Scope(["aa:bb:cc:dd:ee:ff"]),
                     runner=runner, which=lambda x: "hashcat") as worker:
        result = worker.once()
        assert result["status"] == "recovered"
        assert secret not in str(result)
        assert secret not in str(list(worker.conn.execute(
            "SELECT status,reason FROM audit_runs")))
    stored = list(c.results_dir.glob("*.found"))
    assert len(stored) == 1 and stored[0].read_text().strip() == secret
    assert not (stored[0].stat().st_mode & 0o077)


def test_missing_hashcat_reports_unavailable(tmp_path):
    c = make(tmp_path)
    with AuditWorker(c, Scope(["aa:bb:cc:dd:ee:ff"]),
                     which=lambda x: None) as worker:
        assert worker.once()["status"] == "unavailable"


def test_tampered_capture_refused(tmp_path):
    c = make(tmp_path)
    with CaptureIngestor(Settings((tmp_path/"in",), tmp_path/"out", c.database,
                                 settle_seconds=0)) as ing:
        dest = Path(ing.rows()[0]["output_path"])
    dest.write_text("corrupted")
    with AuditWorker(c, Scope(["aa:bb:cc:dd:ee:ff"]),
                     which=lambda x: "hashcat") as worker:
        assert worker.once()["status"] == "idle"
        assert list(worker.conn.execute(
            "SELECT status FROM audit_runs")) == [("error",)]
