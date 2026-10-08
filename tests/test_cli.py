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


def test_scope_cli_add_list_and_clear(tmp_path):
    f = str(tmp_path / "scope.json")
    rc, out = _run(["scope", "--file", f, "list"])
    assert rc == 0 and "EMPTY" in out
    rc, out = _run(["scope", "--file", f, "add", "aa:bb:cc:dd:ee:ff", "--job", "acme", "--label", "my AP"])
    assert rc == 0 and "armed" in out
    rc, out = _run(["scope", "--file", f, "list"])
    assert "aa:bb:cc:dd:ee:ff" in out and "acme" in out
    # persisted to disk as the central store
    from redux.core import Scope
    assert Scope.load(f).permits(bssid="aa:bb:cc:dd:ee:ff") is True
    rc, out = _run(["scope", "--file", f, "clear", "--job", "acme"])
    assert "cleared 1" in out
    assert Scope.load(f).empty is True


def test_scope_cli_arm_lab(tmp_path):
    f = str(tmp_path / "scope.json")
    rc, out = _run(["scope", "--file", f, "arm-lab", "--bssid", "aa:bb:cc:dd:ee:ff", "--cidr", "10.0.0.0/24"])
    assert rc == 0 and "2 target" in out
    from redux.core import Scope
    s = Scope.load(f)
    assert s.permits(bssid="aa:bb:cc:dd:ee:ff") and s.permits(ip="10.0.0.9")


def test_tft_pack_from_config(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[tft]\nface_pack = "owl"\n')
    rc, out = _run(["tft", "--onboard", "--demo", "--face", "plain", "--config", str(cfg)])
    assert rc == 0 and "(@.@)" in out               # owl RUFFLE eyes, driven by config
    rc2, out2 = _run(["tft", "--onboard", "--demo", "--face", "plain"])
    assert rc2 == 0 and "(@.@)" not in out2          # default (augur) shows ‹✺_✺› instead


def test_cache_db_path_from_config(tmp_path):
    db = tmp_path / "sight.db"
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[cache]\ndb_path = "{db}"\n')
    rc, out = _run(["cache", "stats", "--config", str(cfg)])
    assert rc == 0 and "0 sightings" in out
    assert db.exists()                               # opened the configured db, not the default path
