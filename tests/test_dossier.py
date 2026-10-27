"""Target dossier — one-view aggregation about a single AP."""
from redux.intel.dossier import build_dossier


def test_full_dossier_composes_sources():
    d = build_dossier("AA:BB:CC:00:11:22", ssid="HomeNet", security="wpa2-psk",
                      channel=6, rssi=-48, clients=2, pmkid_seen=True)
    assert d.bssid == "aa:bb:cc:00:11:22" and d.ssid == "HomeNet"
    assert d.band == "2.4" and d.crackable and d.value_score > 0
    assert d.pmkid_seen and "crack the PMKID" in d.recommended
    assert any("PMKID already harvested" in f for f in d.facts)


def test_wpa3_transition_dossier_flags_downgrade():
    d = build_dossier("aa:00:00:00:00:01", ssid="Corp", security="wpa3",
                      akm=["SAE", "PSK"], channel=36, rssi=-60)
    assert d.security_mode == "wpa3-transition" and d.downgradable
    assert "WPA2 path" in d.recommended
    assert d.band == "5"


def test_owned_target_recommends_skip():
    d = build_dossier("aa:00:00:00:00:02", security="wpa2-psk",
                      retired={"aa:00:00:00:00:02"})
    assert d.already_owned and "already owned" in d.recommended and d.value_score == 0


def test_open_network_has_no_offline_path():
    d = build_dossier("aa:00:00:00:00:03", security="open")
    assert not d.crackable and "no offline path" in d.recommended


def test_wps_and_clients_shape_the_recommendation():
    d = build_dossier("aa:00:00:00:00:04", security="wpa2-psk", wps=True)
    assert "WPS" in d.recommended
    d2 = build_dossier("aa:00:00:00:00:05", security="wpa2-psk", clients=1)
    assert "deauth" in d2.recommended


def test_location_included_when_known():
    d = build_dossier("aa:00:00:00:00:06", security="wpa2-psk", lat=45.0, lon=-93.0)
    assert d.located == {"lat": 45.0, "lon": -93.0}
    d2 = build_dossier("aa:00:00:00:00:07", security="wpa2-psk")
    assert d2.located is None
