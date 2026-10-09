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


def test_hwtest_runs_software_and_synthetic_battery(tmp_path):
    # no --iface: software self-test + synthetic detection check, no radio needed.
    rc, out = _run(["hwtest"])
    assert rc == 0                                   # all software + synthetic checks pass
    assert "software self-test" in out
    assert "detection pipeline" in out and "deauth_flood=fired" in out
    assert "[SKIP] no --iface" in out                # honest about the radio step


def test_hwtest_writes_report_file(tmp_path):
    rpt = tmp_path / "hw_report.txt"
    rc, out = _run(["hwtest", "--out", str(rpt)])
    assert rc == 0 and rpt.exists()
    assert "end report" in rpt.read_text()


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


def test_init_writes_config_and_keystore(tmp_path, monkeypatch):
    monkeypatch.setenv("AUGUR_PASSPHRASE", "lab-pass")
    rc, out = _run(["init", "--dir", str(tmp_path), "--node-id", "augur-lab-1"])
    assert rc == 0
    from redux.config import AugurConfig
    cfg = AugurConfig.load(tmp_path / "config.toml")
    assert cfg.mesh.node_id == "augur-lab-1" and cfg.validate() == []   # node_id patched + valid
    ks = tmp_path / "swarm.keys"
    assert ks.is_file() and ks.read_bytes().startswith(b"AUGURv1")      # sealed keystore minted


def test_init_without_passphrase_skips_keystore(tmp_path, monkeypatch):
    monkeypatch.delenv("AUGUR_PASSPHRASE", raising=False)
    rc, out = _run(["init", "--dir", str(tmp_path)])
    assert rc == 0 and (tmp_path / "config.toml").is_file()
    assert not (tmp_path / "swarm.keys").exists()        # no passphrase → honestly skipped
    assert "AUGUR_PASSPHRASE" in out                      # told the operator how to finish


def test_bad_config_path_is_clean_not_a_traceback():
    rc, out = _run(["tft", "--onboard", "--demo", "--config", "/no/such/config.toml"])
    assert rc == 4 and "not found" in out and "Traceback" not in out   # clean message, not a crash


def test_init_scope_path_matches_firing_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AUGUR_PASSPHRASE", raising=False)
    monkeypatch.setenv("REDUX_SCOPE", str(tmp_path / "scope.json"))
    rc, out = _run(["init", "--dir", str(tmp_path)])
    # the path init tells the operator to arm is the one the firing tools read
    from redux.cli import _DEFAULT_SCOPE_PATH
    assert rc == 0 and _DEFAULT_SCOPE_PATH in out

def test_live_doctor_uses_local_actual_health_not_synthetic_augmentations(monkeypatch):
    import urllib.request
    served = []
    report = {
        "doctor": {
            "overall": "degraded", "label": "DEGRADED",
            "coverage": {"reason": "storage unknown; no fabricated pass"},
            "findings": [{
                "area": "sighting persistence", "status": "degraded",
                "summary": "Database writes deferred.",
                "reason": "synthetic read-only SQLite disk",
                "remediation": "Check capture storage.",
            }],
        }
    }
    def response(url, timeout):
        served.append((url, timeout))
        return io.BytesIO(json.dumps(report).encode())
    monkeypatch.setattr(urllib.request, "urlopen", response)
    code, output = _run(["live", "doctor", "--port", "8085"])
    assert code == 0
    assert served == [("http://127.0.0.1:8085/api/status", 2)]
    assert "DEGRADED" in output
    assert "sighting persistence" in output
    assert "storage unknown" in output
    assert "Check capture storage." in output


def test_live_doctor_refuses_unavailable_or_unverified_dashboard(monkeypatch):
    import urllib.request
    def unavailable(*args, **kwargs):
        raise OSError("synthetic disconnected service")
    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    rc, output = _run(["live", "doctor"])
    assert rc == 3
    assert "unavailable" in output and "check redux-live.service" in output
    assert "OK" not in output

    def bogus(*args, **kwargs):
        return io.BytesIO(json.dumps({"creature": "demo"}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", bogus)
    rc, output = _run(["live", "doctor"])
    assert rc == 3 and "unavailable" in output


def test_live_doctor_sanitizes_terminal_sequences(monkeypatch):
    import urllib.request
    report = {
        "doctor": {
            "overall": "attention", "label": "ATTENTION",
            "findings": [{
                "area": "capture", "status": "attention",
                "summary": "Danger\x1b[31mRED",
                "reason": "", "remediation": "",
            }],
            "coverage": {"reason": "partial"},
        }
    }
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(report).encode()))
    rc, output = _run(["live", "doctor"])
    assert rc == 0
    assert "\\x1b" not in output
    assert "Danger" in output and "RED" in output


