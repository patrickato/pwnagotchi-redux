"""Ghost — sanitized record/replay.

Pins the honesty guarantee: a ghost carries no real MAC, SSID, hostname, or
coordinate, the mapping is stable and deterministic, and the ghost still replays
exactly like the real session (timing/structure preserved).
"""
import json

from redux.replay import GhostMapper, GhostRecorder, sanitize_events, sanitize_file
from redux.replay.ghost import _looks_like_mac
from redux.core import Augur
from redux.radio import Intent
from redux.engine import BettercapDriver, BettercapConfig, ReplayTransport


_REAL = [
    {"tag": "wifi.ap.new", "time": 1000.0,
     "data": {"mac": "dc:a6:32:11:22:33", "essid": "HomeNet-5G", "hostname": "router.lan",
              "channel": 6, "rssi": -42, "encryption": "WPA2", "lat": 42.1234, "lon": -71.9876}},
    {"tag": "wifi.client.new", "time": 1001.0,
     "data": {"mac": "aa:bb:cc:dd:ee:ff", "ap": "dc:a6:32:11:22:33", "rssi": -55}},
    {"tag": "ble.device.new", "time": 1002.0,
     "data": {"address": "11:22:33:44:55:66", "name": "Patrick's Watch", "gps": [42.1, -71.9]}},
]


def test_identities_are_pseudonymized_and_location_dropped():
    g = sanitize_events(_REAL)
    blob = json.dumps(g)
    # no real identity or coordinate survives anywhere in the output
    for leak in ("dc:a6:32:11:22:33", "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66",
                 "HomeNet-5G", "router.lan", "Patrick's Watch", "42.1", "71.9"):
        assert leak not in blob, f"leaked {leak!r}"
    # location keys are gone; non-identifying telemetry is preserved
    assert "lat" not in g[0]["data"] and "lon" not in g[0]["data"] and "gps" not in g[2]["data"]
    assert g[0]["data"]["channel"] == 6 and g[0]["data"]["rssi"] == -42
    assert g[0]["data"]["encryption"] == "WPA2"
    # tag + time (structure/timing) preserved
    assert [e["tag"] for e in g] == [e["tag"] for e in _REAL]
    assert [e["time"] for e in g] == [e["time"] for e in _REAL]


def test_mapping_is_stable_within_a_session():
    g = sanitize_events(_REAL)
    # the AP's mac appears as data.mac in ev0 and as data.ap in ev1 → same ghost both times
    assert g[0]["data"]["mac"] == g[1]["data"]["ap"]


def test_distinct_identities_get_distinct_ghosts():
    g = sanitize_events(_REAL)
    macs = {g[0]["data"]["mac"], g[1]["data"]["mac"], g[2]["data"]["address"]}
    assert len(macs) == 3


def test_keep_oui_preserves_vendor_prefix():
    g = sanitize_events(_REAL, keep_oui=True)
    assert g[0]["data"]["mac"].startswith("dc:a6:32:")          # OUI kept
    assert g[0]["data"]["mac"] != "dc:a6:32:11:22:33"           # host part changed
    assert _looks_like_mac(g[0]["data"]["mac"])


def test_no_keep_oui_makes_locally_administered_macs():
    g = sanitize_events(_REAL, keep_oui=False)
    first_octet = int(g[0]["data"]["mac"].split(":")[0], 16)
    assert first_octet & 0x02 and not (first_octet & 0x01)      # LAA, unicast
    assert not g[0]["data"]["mac"].startswith("dc:a6:32:")


def test_deterministic_by_seed():
    assert sanitize_events(_REAL, seed="x") == sanitize_events(_REAL, seed="x")
    assert sanitize_events(_REAL, seed="x") != sanitize_events(_REAL, seed="y")


def test_empty_ssid_is_preserved_not_labelled():
    g = sanitize_events([{"tag": "wifi.ap.new", "time": 1.0,
                          "data": {"mac": "aa:bb:cc:dd:ee:ff", "essid": ""}}])
    assert g[0]["data"]["essid"] == ""


def test_mapper_stats_count_without_revealing_originals():
    m = GhostMapper()
    sanitize_events(_REAL, mapper=m)
    s = m.stats()
    assert s["unique_macs"] == 3 and s["unique_names"] == 3   # 2 ssid-ish + 1 ble name


def test_sanitize_file_roundtrip(tmp_path):
    src = tmp_path / "real.json"; dst = tmp_path / "ghost.json"
    src.write_text(json.dumps(_REAL))
    stats = sanitize_file(str(src), str(dst))
    assert stats["events"] == 3 and stats["unique_macs"] == 3
    out = json.loads(dst.read_text())
    assert "HomeNet-5G" not in dst.read_text() and len(out) == 3


# --- the ghost still replays like the real session --------------------------- #

def _run(events):
    driver = BettercapDriver(config=BettercapConfig(), transport=ReplayTransport(events=events))
    bc = Augur(radios=None, intent=Intent.RECON, driver=driver)
    bc.pump()
    return bc


def test_ghost_replays_with_same_shape_and_no_real_identities():
    real_bc = _run(_REAL)
    ghost_bc = _run(sanitize_events(_REAL))
    # same number of sightings recorded (structure preserved)
    assert ghost_bc.store.count() == real_bc.store.count() > 0
    # and nothing real leaked into the replayed store
    leaked = {s.mac for s in ghost_bc.store.query(limit=100)} | \
             {s.ssid for s in ghost_bc.store.query(limit=100)}
    assert "dc:a6:32:11:22:33" not in leaked and "HomeNet-5G" not in leaked


# --- live recorder ----------------------------------------------------------- #

def test_recorder_captures_stream_and_dumps_sanitized(tmp_path):
    driver = BettercapDriver(config=BettercapConfig(), transport=ReplayTransport(events=_REAL))
    bc = Augur(radios=None, intent=Intent.RECON, driver=driver)
    rec = GhostRecorder().attach(bc.bus)
    bc.pump()
    assert len(rec.events()) == len(_REAL)          # captured the live stream
    out = tmp_path / "rec.json"
    stats = rec.dump(str(out), sanitize=True)
    assert stats["sanitized"] is True
    assert "HomeNet-5G" not in out.read_text()      # dumped copy is ghosted
