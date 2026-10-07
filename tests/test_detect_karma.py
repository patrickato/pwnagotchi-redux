"""Hardware-free tests for Karma / captive-twin detector (Grok backlog #3)."""
from __future__ import annotations

from redux.detect import AlertKind, Frame, FrameType, KarmaCaptiveDetector


def probe_resp(ts: float, *,
               bssid: str,
               ssid: str,
               security: str = "open") -> Frame:
    return Frame(
        type=FrameType.PROBE_RESP,
        ts=ts,
        bssid=bssid,
        ssid=ssid,
        security=security,
    )


def beacon(ts: float, *,
           ssid: str,
           bssid: str,
           security: str = "wpa2-psk",
           channel: int | None = 6) -> Frame:
    return Frame(
        type=FrameType.BEACON,
        ts=ts,
        ssid=ssid,
        bssid=bssid,
        security=security,
        channel=channel,
    )


def test_karma_fires_when_one_bssid_answers_many_ssids():
    d = KarmaCaptiveDetector(karma_window_s=15.0, karma_ssid_threshold=5)
    bssid = "aa:bb:cc:dd:ee:01"
    frames = [
        probe_resp(0.1 * i, bssid=bssid, ssid=f"Wanted-{i}") for i in range(5)
    ]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.KARMA
    assert "Karma" in alerts[0].reason or "karma" in alerts[0].reason.lower()
    assert alerts[0].bssid == bssid
    assert alerts[0].detail["unique_ssids"] >= 5


def test_karma_quiet_on_single_ssid_probe_resp():
    d = KarmaCaptiveDetector(karma_window_s=15.0, karma_ssid_threshold=5)
    frames = [
        probe_resp(0.2 * i, bssid="11:22:33:44:55:66", ssid="HomeNet")
        for i in range(20)
    ]
    assert d.feed_many(frames) == []


def test_karma_ignores_beacons_for_karma_path():
    d = KarmaCaptiveDetector(karma_window_s=15.0, karma_ssid_threshold=3)
    # Beacons alone are not Karma (probe-response signature)
    frames = [
        beacon(0.1 * i, ssid=f"S-{i}", bssid="aa:aa:aa:aa:aa:01", security="open")
        for i in range(10)
    ]
    # May still raise captive_twin if mixed security appears — keep same security
    alerts = d.feed_many(frames)
    assert not any(a.kind is AlertKind.KARMA for a in alerts)


def test_captive_twin_fires_open_alongside_wpa2():
    d = KarmaCaptiveDetector()
    frames = [
        beacon(1.0, ssid="CafeWiFi", bssid="11:11:11:11:11:11", security="wpa2-psk"),
        beacon(1.1, ssid="CafeWiFi", bssid="22:22:22:22:22:22", security="open"),
    ]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.CAPTIVE_TWIN
    assert "CafeWiFi" in alerts[0].reason
    assert "open" in alerts[0].reason.lower()
    assert alerts[0].ssid == "CafeWiFi"


def test_captive_twin_quiet_same_security_two_bssids():
    d = KarmaCaptiveDetector()
    frames = [
        beacon(1.0, ssid="Mesh", bssid="11:11:11:11:11:11", security="wpa2-psk"),
        beacon(1.1, ssid="Mesh", bssid="22:22:22:22:22:22", security="wpa2-psk"),
    ]
    assert d.feed_many(frames) == []


def test_captive_twin_quiet_single_bssid():
    d = KarmaCaptiveDetector()
    frames = [
        beacon(1.0, ssid="Solo", bssid="11:11:11:11:11:11", security="open"),
        beacon(2.0, ssid="Solo", bssid="11:11:11:11:11:11", security="open"),
    ]
    assert d.feed_many(frames) == []


def test_captive_twin_dedupes():
    d = KarmaCaptiveDetector()
    f1 = beacon(1.0, ssid="X", bssid="11:11:11:11:11:11", security="wpa2-psk")
    f2 = beacon(1.1, ssid="X", bssid="22:22:22:22:22:22", security="open")
    assert d.feed(f1) is None
    assert d.feed(f2) is not None
    assert d.feed(f2) is None
