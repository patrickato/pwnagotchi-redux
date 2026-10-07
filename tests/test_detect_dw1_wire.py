"""Hardware-free tests for DW1 — full detector wiring."""
from redux.detect import DetectEngine, list_detectors
from redux.detect.frames import Frame, FrameType
from redux.detect.registry import REGISTRY, build_from_registry

EXPECTED = {
    "deauth_flood",
    "beacon_spam",
    "surveillance_sweep",
    "rogue_ap",
    "karma",
    "wps_attack",
    "ble_tracker",
    "handshake",
    "ble_flood",
    "pmf_missing",
    "pineapple",
    "pnl",
    "hidden_ssid",
}


def test_registry_has_all_detectors():
    names = set(list_detectors())
    assert EXPECTED <= names
    assert len(REGISTRY) >= len(EXPECTED)


def test_engine_default_includes_all():
    eng = DetectEngine()
    assert eng.detector_count == len(REGISTRY)


def test_engine_subset():
    eng = DetectEngine(names=["deauth_flood", "pmf_missing"])
    assert eng.detector_count == 2


def test_build_from_registry_runs():
    dets = build_from_registry()
    assert len(dets) == len(REGISTRY)
    # Benign beacon should not crash any detector
    f = Frame(type=FrameType.BEACON, ts=1.0, bssid="11:11:11:11:11:11", ssid="X", security="wpa2-psk", pmf="required")
    for d in dets:
        d.feed(f)


def test_pmf_runs_via_engine():
    eng = DetectEngine(names=["pmf_missing"])
    f = Frame(
        type=FrameType.BEACON,
        ts=1.0,
        bssid="11:22:33:44:55:66",
        ssid="Home",
        security="wpa2-psk",
        pmf="none",
    )
    alerts = eng.feed(f)
    assert any(a.kind.value == "pmf_missing" for a in alerts)


def test_hidden_ssid_runs_via_engine():
    eng = DetectEngine(names=["hidden_ssid"])
    bssid = "aa:bb:cc:dd:ee:ff"
    # cloaked beacon first (empty SSID), then a probe response discloses the name
    eng.feed(Frame(type=FrameType.BEACON, ts=1.0, bssid=bssid, ssid="", security="wpa2-psk"))
    alerts = eng.feed(
        Frame(type=FrameType.PROBE_RESP, ts=2.0, bssid=bssid, ssid="SecretNet", security="wpa2-psk")
    )
    assert any(a.kind.value == "hidden_ssid_reveal" for a in alerts)
