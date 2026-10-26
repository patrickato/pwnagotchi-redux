"""Forensic triage — the baked carving/metadata/stego tools over pulled loot."""
import subprocess

from redux.loot.triage import triage, triage_file, _strings_secrets


def _cp(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, out, err)


def test_strings_secret_scan_is_pure_python(tmp_path):
    f = tmp_path / "config.env"
    f.write_bytes(b"\x00\x01harmless noise\nDB_PASSWORD=hunter2hunter2\nAPI_KEY=ghp_" + b"a" * 24 + b"\n")
    out = _strings_secrets(str(f))
    assert out["available"] is True
    assert out["secret_count"] >= 1
    assert any("PASSWORD" in h.upper() for h in out["secret_hits"])


def test_triage_file_honest_about_missing_tools(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"nothing interesting here")
    # no forensic tools installed -> each marked unavailable, never faked
    r = triage_file(str(f), runner=lambda *a, **k: _cp(a, 0), which=lambda b: None)
    for tool in ("exiftool", "binwalk", "steghide"):
        assert r["tools"][tool]["available"] is False
        assert "not installed" in r["tools"][tool]["reason"]
    assert r["tools"]["strings"]["available"] is True        # pure-python, always runs
    assert r["flagged"] is False


def test_triage_file_runs_present_tools_and_flags_secrets(tmp_path):
    f = tmp_path / "leak.conf"
    f.write_bytes(b"aws_secret_access_key = AKIAfake\n")
    present = {"exiftool", "binwalk", "steghide"}

    def fake_which(b):
        return ("/usr/bin/" + b) if b in present else None

    def fake_runner(argv, **kw):
        if argv[0] == "exiftool":
            return _cp(argv, 0, "[File] File Type : ASCII text")
        if argv[0] == "binwalk":
            return _cp(argv, 0, "DECIMAL  HEXADECIMAL  DESCRIPTION\n0  0x0  ASCII text")
        if argv[0] == "steghide":
            return _cp(argv, 0, "", "steghide: could not extract any data")
        return _cp(argv, 0)

    r = triage_file(str(f), runner=fake_runner, which=fake_which)
    assert r["tools"]["exiftool"]["available"] and "ASCII" in r["tools"]["exiftool"]["metadata"]
    assert r["tools"]["binwalk"]["available"] and "HEXADECIMAL" in r["tools"]["binwalk"]["signatures"]
    assert r["flagged"] is True                              # the aws_secret string flags it


def test_triage_maps_many_files(tmp_path):
    a = tmp_path / "a.txt"; a.write_bytes(b"token=xoxb-123456")
    b = tmp_path / "b.txt"; b.write_bytes(b"clean")
    out = triage([str(a), str(b)], which=lambda x: None)
    assert set(out) == {str(a), str(b)}
    assert out[str(a)]["flagged"] is True and out[str(b)]["flagged"] is False
