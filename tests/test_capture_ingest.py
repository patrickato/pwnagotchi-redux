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


def test_lexical_capture_cursor_survives_restart_and_directory_churn(tmp_path):
    cfg = settings(tmp_path, max_files_per_pass=2)
    inp = cfg.inputs[0]
    for name in ("00", "02", "04", "06", "08"):
        write_file(inp / f"{name}.hc22000", (REC + f"\nsource-{name}\n").encode())
    with CaptureIngestor(cfg) as worker:
        assert [p.name for p in worker._select_batch()] == [
            "00.hc22000", "02.hc22000"
        ]
        assert worker.scan()["scanned"] == 2
        assert worker.db.execute(
            "SELECT value FROM pipeline_meta WHERE key='cursor_path'"
        ).fetchone()[0].endswith("/02.hc22000")

    # A new entry between scanned names must be picked up, while a deleted
    # entry must not strand the persisted cursor at a numeric position.
    write_file(inp / "03.hc22000", (REC + "\nsource-03\n").encode())
    (inp / "04.hc22000").unlink()
    with CaptureIngestor(cfg) as worker:
        assert [p.name for p in worker._select_batch()] == [
            "03.hc22000", "06.hc22000"
        ]
        assert worker.scan()["scanned"] == 2
        assert [p.name for p in worker._select_batch()] == [
            "08.hc22000", "00.hc22000"
        ]
        assert worker.scan()["scanned"] == 2
        assert worker.scan()["scanned"] == 2
        assert worker.db.execute(
            "SELECT value FROM pipeline_meta WHERE key='cursor_path'"
        ).fetchone()[0].endswith("/06.hc22000")


def test_capture_batch_selection_is_streaming_and_bounded(tmp_path, monkeypatch):
    cfg = settings(tmp_path, max_files_per_pass=5)
    with CaptureIngestor(cfg) as worker:
        seen = [0]
        def candidates():
            for index in range(12000):
                seen[0] += 1
                yield f"/captures/incoming/{index:05d}.pcap"
        monkeypatch.setattr(worker, "_candidates", candidates)
        assert worker._select_batch() == [
            f"/captures/incoming/{i:05d}.pcap" for i in range(5)
        ]
        assert seen[0] == 12000
        worker.db.execute(
            "INSERT OR REPLACE INTO pipeline_meta(key,value) "
            "VALUES('cursor_path',?)",
            ("/captures/incoming/11997.pcap",),
        )
        worker.db.commit()
        seen[0] = 0
        assert worker._select_batch() == [
            "/captures/incoming/11998.pcap",
            "/captures/incoming/11999.pcap",
            "/captures/incoming/00000.pcap",
            "/captures/incoming/00001.pcap",
            "/captures/incoming/00002.pcap",
        ]
        # A wrap may stream entries twice, but never needs 12000 path objects
        # resident at the same time; K stays capped at max_files_per_pass.
        assert seen[0] == 24000


def test_old_numeric_cursor_migrates_without_error_or_deleting_data(tmp_path):
    cfg = settings(tmp_path, max_files_per_pass=1)
    for index in range(3):
        write_file(cfg.inputs[0] / f"{index}.hc22000",
                   (REC + f"\nfixture-{index}\n").encode())
    with CaptureIngestor(cfg) as worker:
        worker.db.execute(
            "INSERT OR REPLACE INTO pipeline_meta(key,value) VALUES('cursor','2')"
        )
        worker.db.commit()
        assert [p.name for p in worker._select_batch()] == ["0.hc22000"]
        assert worker.scan()["scanned"] == 1
        cursor = worker.db.execute(
            "SELECT value FROM pipeline_meta WHERE key='cursor_path'"
        ).fetchone()[0]
        assert cursor.endswith("/0.hc22000")
        assert worker.scan()["scanned"] == 1


def test_failed_worker_pass_does_not_advance_lexical_cursor(tmp_path, monkeypatch):
    import pytest
    cfg = settings(tmp_path, max_files_per_pass=2)
    for index in range(3):
        write_file(cfg.inputs[0] / f"{index}.hc22000",
                   (REC + f"\nfile-{index}\n").encode())
    with CaptureIngestor(cfg) as worker:
        processed = [0]
        real = worker.ingest_file
        def crash_on_second(path):
            processed[0] += 1
            if processed[0] == 2:
                raise RuntimeError("synthetic unexpected worker failure")
            return real(path)
        monkeypatch.setattr(worker, "ingest_file", crash_on_second)
        with pytest.raises(RuntimeError, match="synthetic"):
            worker.scan()
        assert worker.db.execute(
            "SELECT value FROM pipeline_meta WHERE key='cursor_path'"
        ).fetchone() is None
        assert worker.db.execute(
            "SELECT value FROM pipeline_meta WHERE key='last_scan'"
        ).fetchone() is None
        monkeypatch.setattr(worker, "ingest_file", real)
        assert [p.name for p in worker._select_batch()] == [
            "0.hc22000", "1.hc22000"
        ]
        assert worker.scan()["scanned"] == 2


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



def test_readonly_worker_health_refuses_symlinked_ledger(tmp_path):
    cfg = settings(tmp_path)
    with CaptureIngestor(cfg) as worker:
        worker.scan()
    shortcut = tmp_path / "ledger-link.db"
    shortcut.symlink_to(cfg.database)
    status = read_summary(shortcut)
    assert status["available"] is False
    assert "symlink" in status["reason"]
    assert read_summary(cfg.database)["available"] is True


