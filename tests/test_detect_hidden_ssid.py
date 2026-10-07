"""Hardware-free tests for hidden-SSID reveal (Grok DD2)."""
from redux.detect.alerts import AlertKind
from redux.detect.frames import Frame, FrameType
from redux.detect.hidden_ssid import HiddenSSIDRevealDetector


def test_reveal_via_probe_resp():
    d = HiddenSSIDRevealDetector()
    d.feed(Frame(type=FrameType.BEACON, ts=1.0, bssid="aa:aa:aa:aa:aa:01", ssid=""))
    a = d.feed(
        Frame(
            type=FrameType.PROBE_RESP,
            ts=2.0,
            bssid="aa:aa:aa:aa:aa:01",
            ssid="SecretNet",
        )
    )
    assert a is not None
    assert a.kind is AlertKind.HIDDEN_SSID_REVEAL
    assert a.ssid == "SecretNet"
    assert a.severity == "info"


def test_no_cloak_no_alert():
    d = HiddenSSIDRevealDetector()
    a = d.feed(
        Frame(
            type=FrameType.PROBE_RESP,
            ts=1.0,
            bssid="aa:aa:aa:aa:aa:01",
            ssid="OpenName",
        )
    )
    assert a is None


def test_dedupe_reveal():
    d = HiddenSSIDRevealDetector()
    d.feed(Frame(type=FrameType.BEACON, ts=1.0, bssid="aa:aa:aa:aa:aa:01", ssid=""))
    assert d.feed(
        Frame(type=FrameType.PROBE_RESP, ts=2.0, bssid="aa:aa:aa:aa:aa:01", ssid="A")
    ) is not None
    assert d.feed(
        Frame(type=FrameType.PROBE_RESP, ts=3.0, bssid="aa:aa:aa:aa:aa:01", ssid="A")
    ) is None
