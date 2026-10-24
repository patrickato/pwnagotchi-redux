"""The already-pwned / ignore list that lab-mode offense retires from."""
import json
import sqlite3

from redux.crack.captured import (normalize_bssid, captured_from_ingest,
                                   IgnoreList, retired_bssids)


def _make_db(tmp_path, ready_bssids):
    db = tmp_path / "jobs.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE artifacts "
                "(status TEXT, hashes INTEGER, bssids TEXT, created_at REAL)")
    con.execute("INSERT INTO artifacts VALUES ('ready', 1, ?, 100.0)",
                (json.dumps(ready_bssids),))
    con.execute("INSERT INTO artifacts VALUES ('invalid', 0, ?, 50.0)",
                (json.dumps(["ffffffffffff"]),))   # not 'ready' -> excluded
    con.commit()
    con.close()
    return str(db)


def test_normalize_bssid():
    assert normalize_bssid("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_bssid("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"   # bare 22000 form
    assert normalize_bssid("nope") is None
    assert normalize_bssid("") is None


def test_captured_from_ingest_reads_ready_only(tmp_path):
    db = _make_db(tmp_path, ["aabbccddee01", "AA:BB:CC:DD:EE:02"])
    got = captured_from_ingest(db)
    assert set(got) == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}   # normalized, invalid excluded


def test_captured_from_ingest_missing_db_fails_safe(tmp_path):
    assert captured_from_ingest(str(tmp_path / "nope.db")) == {}


def test_ignore_list_add_remove_persist(tmp_path):
    p = tmp_path / "captured.json"
    ok, mac = IgnoreList(str(p), now=lambda: 1.0).add("AA:BB:CC:DD:EE:03", ssid="dad24")
    assert ok and mac == "aa:bb:cc:dd:ee:03"
    assert IgnoreList(str(p)).bssids() == {"aa:bb:cc:dd:ee:03"}       # persisted to disk
    assert IgnoreList(str(p)).items()["aa:bb:cc:dd:ee:03"]["ssid"] == "dad24"
    ok, _ = IgnoreList(str(p)).remove("aa:bb:cc:dd:ee:03")
    assert ok and IgnoreList(str(p)).bssids() == set()


def test_ignore_list_rejects_non_mac(tmp_path):
    ok, reason = IgnoreList(str(tmp_path / "c.json")).add("not-a-mac")
    assert not ok and "not a MAC" in reason


def test_ignore_list_missing_file_is_empty(tmp_path):
    assert IgnoreList(str(tmp_path / "absent.json")).bssids() == set()


def test_retired_bssids_unions_capture_and_manual(tmp_path):
    db = _make_db(tmp_path, ["aabbccddee01"])
    p = tmp_path / "captured.json"
    IgnoreList(str(p)).add("aa:bb:cc:dd:ee:09")
    assert retired_bssids(db, str(p)) == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:09"}
