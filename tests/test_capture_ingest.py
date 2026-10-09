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
