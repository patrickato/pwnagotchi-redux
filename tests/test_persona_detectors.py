"""Applying a persona now actually reconfigures which detectors run."""
from redux.core.augur import Augur, _persona_detector_names


def test_resolve_detector_spec():
    assert _persona_detector_names("all") is None            # all registry detectors
    assert _persona_detector_names("none") == []             # explicitly none
    got = _persona_detector_names("rogue_ap, deauth_flood, bogus")
    assert set(got) == {"rogue_ap", "deauth_flood"}          # invalid dropped


def test_detection_only_persona_keeps_all_detectors():
    aug = Augur()
    before = aug.engine.detector_count
    out = aug.apply_persona("blue")                           # detectors="all"
    assert out["changed"]["detectors"]["to"] == before        # still all
    assert "declares" not in out                              # it's applied, not declared


def test_none_persona_disables_detectors_for_real():
    aug = Augur()
    out = aug.apply_persona("mesh")                           # detectors="none"
    assert aug.engine.detector_count == 0                     # engine actually pruned
    assert out["changed"]["detectors"]["to"] == 0


def test_persona_repoints_the_live_pump_engine():
    # Regression: apply_persona must re-point the Supervisor's engine (the one the
    # live pump feeds), not just self.engine — otherwise the pump keeps running the
    # old detector set while the API reports the new one (a glass-box lie).
    aug = Augur()
    assert aug.supervisor._detect_engine.detector_count > 0
    aug.apply_persona("mesh")                                 # detectors="none"
    assert aug.engine.detector_count == 0
    assert aug.supervisor._detect_engine is aug.engine        # same instance
    assert aug.supervisor._detect_engine.detector_count == 0  # pump actually pruned


def test_persona_apply_preserves_the_trusted_set():
    from redux.detect.rogue_ap import parse_trusted_networks
    from redux.detect.frames import Frame, FrameType
    from redux.detect.alerts import AlertKind
    trusted = parse_trusted_networks([{"ssid": "HomeNet", "bssids": ["aa:bb:cc:dd:ee:ff"]}])
    aug = Augur(trusted=trusted)
    aug.apply_persona("blue")                                 # rebuilds the engine
    twin = Frame(type=FrameType.BEACON, ts=1.0, bssid="66:66:66:66:66:66", ssid="HomeNet")
    # the evil-twin detector still fires after the rebuild (trusted not lost)
    assert any(a.kind is AlertKind.ROGUE_AP for a in aug.engine.feed_many([twin]))
