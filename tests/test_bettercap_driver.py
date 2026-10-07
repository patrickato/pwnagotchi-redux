import pytest

from redux.engine import (
    BettercapDriver, BettercapConfig, Allowlist, FiringRefused,
    ReplayTransport, normalize_event, Event,
)


# --- recorded bettercap events (atlas B-12: real-shaped JSON, no radio) ----- #
RECORDED = [
    {"tag": "wifi.ap.new", "time": 1000.0, "data": {"mac": "aa:bb:cc:dd:ee:ff", "essid": "HomeNet"}},
    {"tag": "wifi.client.handshake", "time": 1001.0, "data": {"ap": "aa:bb:cc:dd:ee:ff"}},
    {"tag": "ble.device.new", "time": 1002.0, "data": {"mac": "11:22:33:44:55:66"}},
]


def test_config_base_url_and_bind_scope():
    cfg = BettercapConfig(host="10.0.0.5", port=8080, bind_scope="tailscale")
    assert cfg.base_url == "http://10.0.0.5:8080/api"
    with pytest.raises(ValueError):
        BettercapConfig(bind_scope="everywhere")


def test_normalize_event_maps_and_reasons():
    ev = normalize_event(RECORDED[1])
    assert isinstance(ev, Event)
    assert ev.type == "handshake"
    assert "aa:bb:cc:dd:ee:ff" in ev.reason
    assert ev.raw_tag == "wifi.client.handshake"
    # unknown tag degrades to its tag, not a crash
    assert normalize_event({"tag": "weird.thing", "data": {}}).type == "weird.thing"


def test_set_interface_builds_command_and_logs():
    rec = []
    t = ReplayTransport()
    drv = BettercapDriver(transport=t, log=rec.append)
    drv.set_interface("wlan1")
    drv.recon(True)
    drv.set_channels([1, 6, 11])
    assert t.commands == ["set wifi.interface wlan1", "wifi.recon on", "wifi.recon.channel 1,6,11"]
    assert any("wlan1" in line for line in rec)        # glass-box log fired


def test_poll_events_normalizes_recorded_stream():
    drv = BettercapDriver(transport=ReplayTransport(events=RECORDED))
    evs = drv.poll_events(clear=False)
    assert [e.type for e in evs] == ["ap.new", "handshake", "ble.new"]


def test_stream_events_bounded():
    drv = BettercapDriver(transport=ReplayTransport(events=list(RECORDED)))
    got = list(drv.stream_events(interval=0, _max_polls=1))
    assert len(got) == 3


def test_deauth_refused_when_allowlist_empty():
    rec = []
    drv = BettercapDriver(transport=ReplayTransport(), allowlist=Allowlist(), log=rec.append)
    assert drv.allowlist.empty
    with pytest.raises(FiringRefused) as ei:
        drv.deauth("aa:bb:cc:dd:ee:ff")
    assert "empty" in str(ei.value)
    assert any("REFUSED" in line for line in rec)      # refusal is logged, glass-box


def test_deauth_refused_when_target_not_listed():
    drv = BettercapDriver(transport=ReplayTransport(),
                          allowlist=Allowlist(bssids={"00:11:22:33:44:55"}))
    with pytest.raises(FiringRefused):
        drv.deauth("aa:bb:cc:dd:ee:ff")


def test_deauth_allowed_for_authorized_target():
    t = ReplayTransport()
    drv = BettercapDriver(transport=t, allowlist=Allowlist(bssids={"AA:BB:CC:DD:EE:FF"}))
    drv.deauth("aa:bb:cc:dd:ee:ff")   # case-insensitive match
    assert t.commands == ["wifi.deauth aa:bb:cc:dd:ee:ff"]


def test_allowlist_add_and_permits():
    al = Allowlist()
    al.add(ssid="MyLab")
    al.add(bssid="De:Ad:Be:Ef:00:01")
    assert not al.empty
    assert al.permits(ssid="MyLab")
    assert al.permits(bssid="de:ad:be:ef:00:01")
    assert not al.permits(bssid="de:ad:be:ef:00:02")
