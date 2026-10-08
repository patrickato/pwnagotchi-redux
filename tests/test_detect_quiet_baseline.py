from redux.detect.beacon_spam import BeaconSpamDetector
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.quiet_baseline import benign_home_frames, collect_alerts


def test_core_detectors_quiet_on_home():
    dets = [DeauthFloodDetector(), BeaconSpamDetector()]
    alerts = collect_alerts(dets, benign_home_frames())
    assert alerts == []
