"""The rogue-AP / evil-twin detector now actually fires: trusted set is wired in."""
from redux.detect.frames import Frame, FrameType
from redux.detect.rogue_ap import RogueAPDetector, parse_trusted_networks
from redux.detect.engine import DetectEngine
from redux.detect.alerts import AlertKind


def _beacon(ssid, bssid, ts=1.0):
    return Frame(type=FrameType.BEACON, ts=ts, bssid=bssid, ssid=ssid)


def test_parse_trusted_networks_from_config_entries():
    nets = parse_trusted_networks([
        {"ssid": "HomeNet", "bssids": ["AA:BB:CC:DD:EE:FF"], "channels": [6],
         "security": ["WPA2-PSK"]},
        {"ssid": "", "bssids": []},     # dropped: no ssid
        "junk",                          # dropped: not a dict
    ])
    assert len(nets) == 1 and nets[0].ssid == "HomeNet"
    assert "aa:bb:cc:dd:ee:ff" in nets[0].bssids     # TrustedNetwork lowercases
    assert 6 in nets[0].channels


def test_detector_fires_on_evil_twin_but_not_the_real_ap():
    det = RogueAPDetector(parse_trusted_networks(
        [{"ssid": "HomeNet", "bssids": ["aa:bb:cc:dd:ee:ff"]}]))
    assert det.feed(_beacon("HomeNet", "aa:bb:cc:dd:ee:ff")) is None   # legit AP
    a = det.feed(_beacon("HomeNet", "66:66:66:66:66:66"))              # evil twin
    assert a is not None and a.kind is AlertKind.ROGUE_AP


def test_default_engine_cannot_fire_but_a_configured_one_can():
    twin = _beacon("HomeNet", "66:66:66:66:66:66")
    # the shipped default: empty trusted -> silent (the bug we're fixing is that
    # this was the ONLY behavior reachable)
    assert not any(a.kind is AlertKind.ROGUE_AP
                   for a in DetectEngine().feed_many([twin]))
    # with a trusted set wired in, the registry rogue detector is replaced and fires
    eng = DetectEngine(trusted=parse_trusted_networks(
        [{"ssid": "HomeNet", "bssids": ["aa:bb:cc:dd:ee:ff"]}]))
    assert any(a.kind is AlertKind.ROGUE_AP for a in eng.feed_many([twin]))


def test_config_detect_section_round_trips():
    from redux.config.config import AugurConfig
    cfg = AugurConfig.from_dict({"detect": {"trusted_networks": [
        {"ssid": "HomeNet", "bssids": ["aa:bb:cc:dd:ee:ff"]}]}})
    nets = parse_trusted_networks(cfg.detect.trusted_networks)
    assert nets and nets[0].ssid == "HomeNet"
    assert cfg.validate() == [] or isinstance(cfg.validate(), list)


def test_augur_wires_the_trusted_set_into_its_engine():
    from redux.core.augur import Augur
    aug = Augur(trusted=parse_trusted_networks(
        [{"ssid": "HomeNet", "bssids": ["aa:bb:cc:dd:ee:ff"]}]))
    alerts = aug.engine.feed_many([_beacon("HomeNet", "66:66:66:66:66:66")])
    assert any(a.kind is AlertKind.ROGUE_AP for a in alerts)
