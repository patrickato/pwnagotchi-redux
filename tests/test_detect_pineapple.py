"""Hardware-free tests for Pineapple detector (Grok D2)."""
from redux.detect.alerts import AlertKind
from redux.detect.frames import Frame, FrameType
from redux.detect.pineapple import PineappleDetector


def test_probe_resp_many_ssids():
    d = PineappleDetector(probe_ssid_threshold=5, beacon_ssid_threshold=100)
    frames = [
        Frame(
            type=FrameType.PROBE_RESP,
            ts=0.1 * i,
            bssid="aa:aa:aa:aa:aa:01",
            ssid=f"S{i}",
        )
        for i in range(5)
    ]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.PINEAPPLE


def test_quiet_single_ssid():
    d = PineappleDetector(probe_ssid_threshold=5)
    frames = [
        Frame(type=FrameType.PROBE_RESP, ts=float(i), bssid="aa:aa:aa:aa:aa:01", ssid="Home")
        for i in range(20)
    ]
    assert d.feed_many(frames) == []
