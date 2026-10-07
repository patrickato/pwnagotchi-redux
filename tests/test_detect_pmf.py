"""Hardware-free tests for PMF-missing detector (Grok D3)."""
from redux.detect.alerts import AlertKind
from redux.detect.frames import Frame, FrameType
from redux.detect.pmf import PMFMissingDetector


def test_wpa2_no_pmf_fires():
    d = PMFMissingDetector()
    a = d.feed(
        Frame(
            type=FrameType.BEACON,
            ts=1.0,
            bssid="11:22:33:44:55:66",
            ssid="Home",
            security="wpa2-psk",
            pmf="none",
        )
    )
    assert a is not None
    assert a.kind is AlertKind.PMF_MISSING
    assert a.severity == "info"


def test_pmf_required_quiet():
    d = PMFMissingDetector()
    a = d.feed(
        Frame(
            type=FrameType.BEACON,
            ts=1.0,
            bssid="11:22:33:44:55:66",
            ssid="Home",
            security="wpa2-psk",
            pmf="required",
        )
    )
    assert a is None


def test_open_ignored():
    d = PMFMissingDetector()
    a = d.feed(
        Frame(
            type=FrameType.BEACON,
            ts=1.0,
            bssid="11:22:33:44:55:66",
            ssid="Open",
            security="open",
            pmf="",
        )
    )
    assert a is None
