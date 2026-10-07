"""Hardware-free tests for PMKID / handshake capture observer."""
from __future__ import annotations

from redux.detect import AlertKind, Frame, FrameType, HandshakeCaptureDetector


def pmkid(ts: float, bssid: str = "11:22:33:44:55:66", ssid: str = "Home") -> Frame:
    return Frame(type=FrameType.PMKID, ts=ts, bssid=bssid, ssid=ssid)


def eapol(
    ts: float,
    msg: int,
    bssid: str = "11:22:33:44:55:66",
    ssid: str = "Home",
) -> Frame:
    return Frame(
        type=FrameType.EAPOL, ts=ts, bssid=bssid, ssid=ssid, eapol_msg=msg
    )


def test_pmkid_fires_once():
    d = HandshakeCaptureDetector()
    a = d.feed(pmkid(1.0))
    assert a is not None
    assert a.kind is AlertKind.PMKID_CAPTURE
    assert "PMKID" in a.reason
    assert a.severity == "info"
    assert d.feed(pmkid(2.0)) is None  # dedupe


def test_handshake_fires_on_m1_to_m4():
    d = HandshakeCaptureDetector(handshake_window_s=30.0)
    frames = [eapol(float(i), msg=i) for i in range(1, 5)]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.HANDSHAKE_CAPTURE
    assert "M1–M4" in alerts[0].reason or "handshake" in alerts[0].reason.lower()
    assert alerts[0].detail["messages"] == [1, 2, 3, 4]


def test_partial_eapol_quiet():
    d = HandshakeCaptureDetector()
    frames = [eapol(1.0, 1), eapol(2.0, 2), eapol(3.0, 3)]  # no M4
    assert d.feed_many(frames) == []


def test_stale_eapol_outside_window():
    d = HandshakeCaptureDetector(handshake_window_s=5.0)
    frames = [
        eapol(0.0, 1),
        eapol(1.0, 2),
        eapol(2.0, 3),
        eapol(20.0, 4),  # M1–M3 expired
    ]
    assert d.feed_many(frames) == []


def test_ignores_beacons():
    d = HandshakeCaptureDetector()
    f = Frame(type=FrameType.BEACON, ts=1.0, bssid="11:22:33:44:55:66", ssid="X")
    assert d.feed(f) is None


def test_handshake_dedupes_per_bssid():
    d = HandshakeCaptureDetector()
    frames = [eapol(float(i), msg=i) for i in range(1, 5)]
    assert len(d.feed_many(frames)) == 1
    again = [eapol(10.0 + i, msg=i) for i in range(1, 5)]
    assert d.feed_many(again) == []
