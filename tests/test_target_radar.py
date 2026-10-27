"""Target-value radar — rank APs by how worth attacking each is."""
from redux.intel.target_radar import (
    TargetObservation, score_target, rank, summary,
)


def _obs(bssid, **kw):
    return TargetObservation(bssid=bssid, **kw)


def test_crackable_ap_scores_and_explains():
    t = score_target(_obs("aa:bb:cc:00:00:01", ssid="Home", security="wpa2-psk", rssi=-50))
    assert t.crackable and t.score > 0
    assert any("offline-crackable" in r for r in t.reasons)
    assert any("signal" in r for r in t.reasons)


def test_pmkid_seen_boosts_score():
    base = score_target(_obs("aa:bb:cc:00:00:01", security="wpa2-psk", rssi=-60))
    withp = score_target(_obs("aa:bb:cc:00:00:01", security="wpa2-psk", rssi=-60, pmkid_seen=True))
    assert withp.score > base.score
    assert any("PMKID" in r for r in withp.reasons)


def test_already_owned_scores_zero():
    t = score_target(_obs("aa:bb:cc:00:00:01", security="wpa2-psk", rssi=-40),
                     retired={"AA:BB:CC:00:00:01"})
    assert t.score == 0 and t.already_owned and "already owned" in t.reasons[0]


def test_uncrackable_open_network_scores_zero():
    t = score_target(_obs("aa:bb:cc:00:00:02", security="open", rssi=-40))
    assert t.score == 0 and not t.crackable


def test_clients_and_wps_add_value():
    quiet = score_target(_obs("aa:bb:cc:00:00:03", security="wpa2-psk", rssi=-60))
    busy = score_target(_obs("aa:bb:cc:00:00:03", security="wpa2-psk", rssi=-60,
                             clients=3, wps=True))
    assert busy.score > quiet.score
    assert any("client" in r for r in busy.reasons) and any("WPS" in r for r in busy.reasons)


def test_rank_orders_juiciest_first():
    obs = [
        _obs("aa:00:00:00:00:01", ssid="weak", security="wpa2-psk", rssi=-85),
        _obs("aa:00:00:00:00:02", ssid="prime", security="wpa2-psk", rssi=-45,
             pmkid_seen=True, clients=2),
        _obs("aa:00:00:00:00:03", ssid="open", security="open", rssi=-40),
    ]
    ranked = rank(obs)
    assert ranked[0].ssid == "prime"          # strong + free hash + clients wins
    assert ranked[-1].score == 0              # the open net sinks to the bottom


def test_summary_names_the_first_target():
    obs = [_obs("aa:00:00:00:00:02", ssid="prime", security="wpa2-psk", rssi=-45, pmkid_seen=True)]
    s = summary(rank(obs))
    assert s["workable"] == 1 and s["top"]["ssid"] == "prime" and "hit prime first" in s["note"]
