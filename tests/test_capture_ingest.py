from pathlib import Path
import os
import subprocess
import time

from redux.crack.ingest import CaptureIngestor, Settings, normalize_22000, valid_22000_line, read_summary

MAC1 = "aabbccddeeff"
MAC2 = "112233445566"
PMKID = "00" * 16
REC = f"WPA*01*{PMKID}*{MAC1}*{MAC2}*74657374***"
REC2 = f"WPA*02*{PMKID}*{MAC1}*{MAC2}*74657374*" + ("11"*32) + "*" + ("ab"*64) + "*00"


def settings(tmp_path, **kw):
    inp = tmp_path / "in"
    inp.mkdir(exist_ok=True)
    return Settings((inp,), tmp_path / "out", tmp_path / "state" / "jobs.db",
                    settle_seconds=0, **kw)


def write_file(path, contents):
    path.write_bytes(contents)
    os.utime(path, (time.time()-10, time.time()-10))
    return path


def test_record_validation():
    assert valid_22000_line(REC)
    assert valid_22000_line(REC2)
    assert not valid_22000_line("random")
    assert not valid_22000_line(REC.replace(MAC1, "zz"*6))
    assert not valid_22000_line("WPA*01*" + "00"*16 + "*" + MAC1 + "*" + MAC2 + "*74657374**")


def test_normalize_and_dedup_records():
    lines, macs = normalize_22000((REC+"\n"+REC+"\n"+REC2+"\n").encode())
    assert len(lines) == 2
    assert macs == [MAC1]


def test_ingest_existing_22000(tmp_path):
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "a.hc22000", (REC + "\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "ready"
        assert ing.ingest_file(p) == "duplicate"
        rows = ing.rows()
        assert len(rows) == 1 and rows[0]["status"] == "ready"
        assert (cfg.output_dir / (rows[0]["output_sha"]+".hc22000")).read_text() == REC+"\n"
        assert rows[0]["hashes"] == 1


def test_content_dedup_different_names(tmp_path):
    cfg = settings(tmp_path)
    a = write_file(cfg.inputs[0] / "a.hc22000", (REC + "\n").encode())
    b = write_file(cfg.inputs[0] / "b.hc22000", (REC.lower().replace("wpa", "WPA") + "\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(a) == "ready"
        assert ing.ingest_file(b) == "duplicate"


def test_invalid_capture(tmp_path):
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "bad.hc22000", b"not a WPA22000 record\n")
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "invalid"
        assert not list(cfg.output_dir.glob("*.hc22000"))


def test_convert_with_injected_runner(tmp_path):
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "a.pcapng", b"fixture-pcapng-data")
    seen = []
    def fake_run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        Path(cmd[2]).write_text(REC+"\n")
        return subprocess.CompletedProcess(cmd, 0)
    with CaptureIngestor(cfg, runner=fake_run) as ing:
        assert ing.ingest_file(p) == "ready"
    assert seen and seen[0][0][0] == "hcxpcapngtool"
    assert seen[0][1]["timeout"] == cfg.convert_timeout_seconds


def test_converter_missing_is_retryable(tmp_path):
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "a.pcap", b"fixture-pcap-data")
    def missing(*a, **kw):
        raise FileNotFoundError("hcxpcapngtool")
    with CaptureIngestor(cfg, runner=missing) as ing:
        assert ing.ingest_file(p) == "error"
        assert ing.ingest_file(p) == "error"
        assert ing.rows()[0]["status"] == "error"


def test_defer_new_files(tmp_path):
    cfg = Settings((tmp_path / "in",), tmp_path / "out", tmp_path / "state" / "jobs.db",
                   settle_seconds=10)
    cfg.inputs[0].mkdir()
    p = cfg.inputs[0] / "a.hc22000"
    p.write_text(REC+"\n")
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "deferred"


def test_symlinks_not_followed(tmp_path):
    cfg = settings(tmp_path)
    private = write_file(tmp_path / "private.hc22000", (REC+"\n").encode())
    link = cfg.inputs[0] / "link.hc22000"
    link.symlink_to(private)
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(link) == "skipped"
        assert ing.scan()["scanned"] == 0


def test_persistent_resume(tmp_path):
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "a.hc22000", (REC+"\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "ready"
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "duplicate"
        assert ing.summary()["by_status"]["ready"] == 1


def test_missing_prepared_artifact_regenerated_from_source(tmp_path):
    cfg = settings(tmp_path)
    original = write_file(cfg.inputs[0] / "recover.hc22000", (REC + "\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(original) == "ready"
        artifact = Path(ing.rows()[0]["output_path"])
        artifact.unlink()
        assert ing.ingest_file(original) == "ready"
        assert artifact.read_text() == REC + "\n"
        assert len(ing.rows()) == 1
        assert ing.rows()[0]["status"] == "ready"


def test_tampered_prepared_artifact_is_not_silently_replaced(tmp_path):
    cfg = settings(tmp_path)
    original = write_file(cfg.inputs[0] / "tamper.hc22000", (REC + "\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(original) == "ready"
        artifact = Path(ing.rows()[0]["output_path"])
        artifact.write_bytes(b"tampered artifact contents")
        assert ing.ingest_file(original) == "error"
        assert artifact.read_bytes() == b"tampered artifact contents"
        assert "altered" in ing.rows()[0]["reason"]


def test_replaced_artifact_with_symlink_refused(tmp_path):
    cfg = settings(tmp_path)
    original = write_file(cfg.inputs[0] / "symlink.hc22000", (REC + "\n").encode())
    elsewhere = tmp_path / "outside"
    elsewhere.write_bytes(b"do not touch me")
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(original) == "ready"
        artifact = Path(ing.rows()[0]["output_path"])
        artifact.unlink()
        artifact.symlink_to(elsewhere)
        assert ing.ingest_file(original) == "error"
        assert elsewhere.read_bytes() == b"do not touch me"
        assert ing.rows()[0]["status"] == "error"


def test_input_disappears_during_digest_is_deferred(tmp_path, monkeypatch):
    from redux.crack import ingest as module
    cfg = settings(tmp_path)
    source = write_file(cfg.inputs[0] / "race.hc22000", (REC + "\n").encode())
    def disappearing(path):
        raise FileNotFoundError("file moved by writer")
    monkeypatch.setattr(module, "_digest_file", disappearing)
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(source) == "deferred"
        assert ing.rows() == []


def test_changed_capture_creates_new_record(tmp_path):
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "a.hc22000", (REC+"\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "ready"
        write_file(p, (REC2+"\n").encode())
        assert ing.ingest_file(p) == "ready"
        assert len(ing.rows()) == 2


def test_scan_cursor_progresses_when_more_than_limit(tmp_path):
    cfg = settings(tmp_path, max_files_per_pass=2)
    for i in range(5):
        write_file(cfg.inputs[0] / f"{i}.hc22000", (REC + f"\nINVALID-{i}\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.scan()["scanned"] == 2
        assert ing.scan()["scanned"] == 2
        assert ing.scan()["scanned"] == 2
        assert len(ing.rows()) == 5


def test_no_input_no_crash(tmp_path):
    cfg = Settings((tmp_path / "missing",), tmp_path / "out", tmp_path / "db" / "state.db")
    with CaptureIngestor(cfg) as ing:
        assert ing.scan()["scanned"] == 0


def test_output_not_world_readable(tmp_path):
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "a.hc22000", (REC+"\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "ready"
        output = list(cfg.output_dir.glob("*.hc22000"))[0]
        assert output.stat().st_mode & 0o077 == 0
        assert cfg.database.stat().st_mode & 0o077 == 0


def test_readonly_summary_missing_and_live(tmp_path):
    assert read_summary(tmp_path/"not-here.db")["available"] is False
    cfg = settings(tmp_path)
    p = write_file(cfg.inputs[0] / "one.hc22000", (REC+"\n").encode())
    with CaptureIngestor(cfg) as ing:
        assert ing.ingest_file(p) == "ready"
    data = read_summary(cfg.database)
    assert data["available"] is True
    assert data["hash_records"] == 1
    assert data["by_status"] == {"ready":1}
    assert "bssids" not in data


def test_low_storage_pauses_without_losing_input(tmp_path, monkeypatch):
    from redux.crack import ingest
    cfg = settings(tmp_path)
    file = write_file(cfg.inputs[0] / "capture.hc22000", (REC + "\n").encode())
    with CaptureIngestor(cfg) as worker:
        monkeypatch.setattr(ingest, "free_bytes", lambda path: 1000)
        result = worker.scan()
        assert result["outcomes"] == {"paused_low_storage": 1}
        assert worker.ingest_file(file) == "deferred_low_storage"
        assert file.is_file()
        assert worker.rows() == []


def test_refuse_output_symlink(tmp_path):
    import pytest
    inp = tmp_path/"in"; inp.mkdir()
    elsewhere = tmp_path/"elsewhere"; elsewhere.mkdir()
    out = tmp_path/"out"; out.symlink_to(elsewhere)
    cfg = Settings((inp,), out, tmp_path/"db"/"jobs.db")
    with pytest.raises(ValueError, match="symlink"):
        CaptureIngestor(cfg)

def test_scan_heartbeat_survives_worker_restart_including_empty_pass(tmp_path):
    cfg = settings(tmp_path)
    with CaptureIngestor(cfg, clock=lambda: 1_234_567.0) as worker:
        first = worker.scan()
        assert first["scanned"] == 0
        assert first["outcomes"] == {}
        snap = read_summary(cfg.database)
        assert snap["last_scan"] == {
            "completed_utc": 1_234_567.0, "scanned": 0, "outcomes": {},
        }
    with CaptureIngestor(cfg, clock=lambda: 1_234_610.0) as worker:
        source = write_file(cfg.inputs[0] / "record.hc22000", (REC + "\n").encode())
        os.utime(source, (1000, 1000))  # file predates injected worker clock
        result = worker.scan()
        assert result["outcomes"]["ready"] == 1
    latest = read_summary(cfg.database)
    assert latest["last_scan"]["completed_utc"] == 1_234_610.0
    assert latest["last_scan"]["scanned"] == 1
    assert latest["last_scan"]["outcomes"] == {"ready": 1}
    assert latest["hash_records"] == 1


def test_low_storage_scan_creates_durable_pause_heartbeat(tmp_path, monkeypatch):
    from redux.crack import ingest
    cfg = settings(tmp_path)
    source = write_file(cfg.inputs[0] / "do_not_delete.hc22000",
                        (REC + "\n").encode())
    with CaptureIngestor(cfg, clock=lambda: 42.0) as worker:
        monkeypatch.setattr(ingest, "free_bytes", lambda path: 1024)
        assert worker.scan()["outcomes"] == {"paused_low_storage": 1}
        snapshot = read_summary(cfg.database)
        assert snapshot["last_scan"] == {
            "completed_utc": 42.0, "scanned": 0,
            "outcomes": {"paused_low_storage": 1},
        }
        assert source.is_file()
        assert worker.rows() == []


def test_failed_processing_is_recorded_without_claiming_success(tmp_path):
    cfg = settings(tmp_path)
    source = write_file(cfg.inputs[0] / "bad.hc22000", b"not a valid converted capture\n")
    os.utime(source, (100, 100))
    with CaptureIngestor(cfg, clock=lambda: 200.0) as worker:
        outcome = worker.scan()
        assert outcome["outcomes"] == {"invalid": 1}
    snap = read_summary(cfg.database)
    assert snap["last_scan"]["scanned"] == 1
    assert snap["last_scan"]["outcomes"] == {"invalid": 1}
    assert snap["hash_records"] == 0


def test_corrupted_scan_metadata_is_not_presented_as_healthy(tmp_path):
    cfg = settings(tmp_path)
    with CaptureIngestor(cfg) as worker:
        worker.scan()
        worker.db.execute(
            "UPDATE pipeline_meta SET value=? WHERE key='last_scan'",
            ('{"schema":1,"completed_utc":"yesterday","scanned":0,"outcomes":{}}',))
        worker.db.commit()
    scan = read_summary(cfg.database)
    assert scan["available"] is True
    assert scan["last_scan"] is None
    assert scan["scan_error"] == "invalid stored scan record"


