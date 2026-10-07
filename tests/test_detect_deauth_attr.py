"""Hardware-free tests for deauth attribution (Grok DD1)."""
from redux.detect.deauth_attr import DeauthAttributionDetector
from redux.detect.frames import Frame, FrameType


def deauth(ts: float, src: str, bssid: str, dst: str = "") -> Frame:
    return Frame(
        type=FrameType.DEAUTH,
        ts=ts,
        src=src,
        bssid=bssid,
        dst=dst or bssid,
    )


def test_attributes_aggressor():
    d = DeauthAttributionDetector(window_s=5.0, threshold=5)
    frames = [
        deauth(0.1 * i, src="aa:aa:aa:aa:aa:99", bssid="11:11:11:11:11:11", dst="22:22:22:22:22:22")
        for i in range(5)
    ]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].detail["aggressor"] == "aa:aa:aa:aa:aa:99"
    assert "aggressor STA" in alerts[0].reason


def test_quiet_below_threshold():
    d = DeauthAttributionDetector(threshold=10)
    frames = [deauth(float(i), "aa:aa:aa:aa:aa:99", "11:11:11:11:11:11") for i in range(3)]
    assert d.feed_many(frames) == []


def test_ignores_beacons():
    d = DeauthAttributionDetector(threshold=1)
    f = Frame(type=FrameType.BEACON, ts=1.0, bssid="11:11:11:11:11:11", ssid="X")
    assert d.feed(f) is None
