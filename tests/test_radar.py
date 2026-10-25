"""Threat radar — counter-surveillance detections (engine + CLI)."""
from types import SimpleNamespace

from redux.intel.radar import scan_sightings, Alert, RadarReport


def _r(ssid, mac, rssi=-70, first_seen=0.0, kind="wifi"):
    return SimpleNamespace(kind=kind, ssid=ssid, mac=mac, rssi=rssi, first_seen=first_seen)


def _kinds(rep):
    return {a.kind for a in rep.alerts}


def test_evil_twin_one_ssid_two_bssids():
    rep = scan_sightings([_r("HomeNet", "aa:bb:cc:dd:ee:01"),
                          _r("HomeNet", "aa:bb:cc:dd:ee:02")], now=1000.0)
    ev = [a for a in rep.alerts if a.kind == "evil_twin"]
    assert len(ev) == 1 and ev[0].severity == "medium"
    assert ev[0].bssids == ("aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02")


def test_evil_twin_impersonating_trusted_is_high():
    rep = scan_sightings([_r("HomeNet", "aa:bb:cc:dd:ee:01"),
                          _r("HomeNet", "99:99:99:99:99:99")],
                         trusted_bssids=["aa:bb:cc:dd:ee:01"], now=1000.0)
    ev = [a for a in rep.alerts if a.kind == "evil_twin"][0]
    assert ev.severity == "high" and "cloned" in ev.detail


def test_pnl_lure_flags_sought_network_from_unknown_bssid():
    rep = scan_sightings([_r("CoffeeShop", "11:22:33:44:55:66")],
                         pnl_ssids=["CoffeeShop"], now=1000.0)
    assert _kinds(rep) == {"pnl_lure"}
    assert rep.alerts[0].severity == "medium"


def test_pnl_lure_not_raised_for_trusted_bssid():
    rep = scan_sightings([_r("CoffeeShop", "11:22:33:44:55:66")],
                         pnl_ssids=["CoffeeShop"],
                         trusted_bssids=["11:22:33:44:55:66"], now=1000.0)
    assert not rep.alerts


def test_new_strong_ap_recent_and_close():
    rep = scan_sightings([_r("NewAP", "de:ad:be:ef:00:01", rssi=-40, first_seen=990.0)],
                         now=1000.0, new_window_s=3600, strong_rssi=-50)
    assert _kinds(rep) == {"new_strong_ap"} and rep.alerts[0].severity == "low"


def test_old_or_weak_ap_is_not_flagged():
    rep = scan_sightings([_r("Quiet", "00:11:22:33:44:55", rssi=-85, first_seen=0.0)],
                         now=1_000_000.0)
    assert not rep.alerts


def test_report_always_notes_the_frame_level_blind_spot():
    rep = scan_sightings([], now=1000.0)
    assert rep.scanned_aps == 0 and rep.alerts == ()
    assert rep.notes and any("deauth" in n and "jamming" in n for n in rep.notes)


def test_alerts_sorted_high_first():
    rep = scan_sightings([
        _r("NewAP", "de:ad:be:ef:00:01", rssi=-40, first_seen=990.0),   # low
        _r("HomeNet", "aa:bb:cc:dd:ee:01"), _r("HomeNet", "99:99:99:99:99:99"),  # high
    ], trusted_bssids=["aa:bb:cc:dd:ee:01"], now=1000.0)
    assert rep.alerts[0].severity == "high"


# --- CLI -------------------------------------------------------------------- #

def test_cmd_radar_detects_evil_twin_from_store(tmp_path, capsys):
    from redux.cli import cmd_radar
    from redux.geo.db import SightingStore, Sighting
    from redux.core.scope import Scope
    import json as _json
    db = tmp_path / "s.db"
    store = SightingStore(str(db))
    store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:01", ssid="HomeNet", ts=1.0, provenance="t"))
    store.insert(Sighting(kind="wifi", mac="99:88:77:66:55:44", ssid="HomeNet", ts=2.0, provenance="t"))
    scope_file = tmp_path / "scope.json"
    Scope(path=str(scope_file)).save()                 # empty -> no trusted BSSIDs
    base = dict(db=str(db), pnl=None, trusted=None, scope_file=str(scope_file),
                new_window_hours=1.0, strong_rssi=-50)
    assert cmd_radar(SimpleNamespace(json=False, **base)) == 0
    out = capsys.readouterr().out
    assert "THREAT RADAR" in out and "evil_twin" in out and "HomeNet" in out
    assert cmd_radar(SimpleNamespace(json=True, **base)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert any(a["kind"] == "evil_twin" for a in payload["alerts"])
    assert payload["notes"]


def test_cmd_radar_quiet_store_is_honest(tmp_path, capsys):
    from redux.cli import cmd_radar
    from redux.core.scope import Scope
    scope_file = tmp_path / "scope.json"
    Scope(path=str(scope_file)).save()
    assert cmd_radar(SimpleNamespace(db=str(tmp_path / "none.db"), pnl=None, trusted=None,
                                     scope_file=str(scope_file), new_window_hours=1.0,
                                     strong_rssi=-50, json=False)) == 0
    assert "nothing suspicious" in capsys.readouterr().out
