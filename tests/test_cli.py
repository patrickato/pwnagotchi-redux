import json
import io
import contextlib

from redux.cli import main


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue()


def test_status_prints_snapshot():
    rc, out = _run(["status", "--onboard", "--adapter", "--intent", "hunt"])
    assert rc == 0
    data = json.loads(out)
    assert data["intent"] == "hunt"
    assert data["capture_iface"] == "wlan1"
    assert "reason" in data["recommendation"]


def test_run_over_replayed_events(tmp_path):
    evs = [
        {"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "aa:bb:cc:dd:ee:01", "essid": "Home", "channel": 6}},
        {"tag": "wifi.ap.new", "time": 2.0, "data": {"mac": "aa:bb:cc:dd:ee:02", "essid": "Cafe", "channel": 11}},
    ]
    f = tmp_path / "events.json"
    f.write_text(json.dumps(evs))
    rc, out = _run(["run", "--onboard", "--adapter", "--intent", "hunt", "--replay", str(f), "--cycles", "1"])
    assert rc == 0
    data = json.loads(out)
    assert data["sightings"] == 2            # both APs geo-tagged into the store
    assert "narration" in data


def test_packs_list_and_enable(tmp_path):
    (tmp_path / "geo").mkdir()
    (tmp_path / "geo" / "pack.json").write_text(json.dumps({"name": "geo", "kind": "suite"}))
    (tmp_path / "viz").mkdir()
    (tmp_path / "viz" / "pack.json").write_text(json.dumps({"name": "viz", "kind": "suite", "requires": ["geo"]}))

    rc, out = _run(["packs", "--dir", str(tmp_path), "list"])
    assert rc == 0 and "geo" in out and "viz" in out

    rc, out = _run(["packs", "--dir", str(tmp_path), "enable", "viz"])
    assert rc == 0 and "geo" in out and "viz" in out   # enabling viz pulls in geo

    rc, out = _run(["packs", "--dir", str(tmp_path), "list"])
    assert "[*] viz" in out and "[*] geo" in out


def test_packs_missing_dependency_errors(tmp_path):
    (tmp_path / "viz").mkdir()
    (tmp_path / "viz" / "pack.json").write_text(json.dumps({"name": "viz", "requires": ["geo"]}))
    rc, _ = _run(["packs", "--dir", str(tmp_path), "enable", "viz"])
    assert rc == 1
