"""Hardware-free tests for CSA abuse detector (Grok DD3)."""
from redux.detect.alerts import AlertKind
from redux.detect.csa import CSAAbuseDetector
from redux.detect.frames import Frame, FrameType


def csa(ts: float, bssid: str = "aa:aa:aa:aa:aa:01", new_ch: int = 6) -> Frame:
    return Frame(
        type=FrameType.CSA,
        ts=ts,
        bssid=bssid,
        csa_new_channel=new_ch,
        channel=1,
    )


def test_rapid_csa_fires():
    d = CSAAbuseDetector(window_s=30.0, threshold=4)
    frames = [csa(float(i), new_ch=6 + (i % 3)) for i in range(4)]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.CSA_ABUSE
    assert "channel switches" in alerts[0].reason


def test_quiet_single_csa():
    d = CSAAbuseDetector(threshold=4)
    assert d.feed(csa(1.0)) is None


def test_ignores_beacon():
    d = CSAAbuseDetector(threshold=1)
    f = Frame(type=FrameType.BEACON, ts=1.0, bssid="aa:aa:aa:aa:aa:01", ssid="X")
    assert d.feed(f) is None
