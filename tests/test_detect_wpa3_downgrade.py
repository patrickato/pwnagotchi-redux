"""Hardware-free tests for WPA3-downgrade detector (Grok DD4)."""
from redux.detect.alerts import AlertKind
from redux.detect.frames import Frame, FrameType
from redux.detect.wpa3_downgrade import WPA3DowngradeDetector


def beacon(ts: float, bssid: str, sec: str, ssid: str = "Net") -> Frame:
    return Frame(type=FrameType.BEACON, ts=ts, bssid=bssid, ssid=ssid, security=sec)


def test_transition_mode_fires():
    d = WPA3DowngradeDetector()
    a = d.feed(beacon(1.0, "11:11:11:11:11:11", "wpa3-sae-transition-wpa2-psk"))
    assert a is not None
    assert a.kind is AlertKind.WPA3_DOWNGRADE
    assert a.detail["mode"] == "transition"


def test_downgrade_from_wpa3_only():
    d = WPA3DowngradeDetector()
    assert d.feed(beacon(1.0, "11:11:11:11:11:11", "wpa3-sae")) is None
    a = d.feed(beacon(2.0, "11:11:11:11:11:11", "wpa2-psk"))
    assert a is not None
    assert a.detail["mode"] == "downgrade"
    assert a.severity == "critical"


def test_stable_wpa3_quiet():
    d = WPA3DowngradeDetector()
    frames = [beacon(float(i), "11:11:11:11:11:11", "wpa3-sae") for i in range(5)]
    assert d.feed_many(frames) == []
