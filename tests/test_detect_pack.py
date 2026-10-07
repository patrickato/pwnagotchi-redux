"""Hardware-free tests for the defensive detector pack (Grok lane)."""
from __future__ import annotations

import pytest

from redux.detect import (
    AlertKind,
    BeaconSpamDetector,
    DeauthFloodDetector,
    Frame,
    FrameType,
    RogueAPDetector,
    TrustedNetwork,
)


# --- helpers -----------------------------------------------------------------

def deauth(ts: float, bssid: str = "aa:bb:cc:dd:ee:01") -> Frame:
    return Frame(type=FrameType.DEAUTH, ts=ts, bssid=bssid, src=bssid)


def disassoc(ts: float, bssid: str = "aa:bb:cc:dd:ee:01") -> Frame:
    return Frame(type=FrameType.DISASSOC, ts=ts, bssid=bssid, src=bssid)


def beacon(
    ts: float,
    *,
    ssid: str = "HomeNet",
    bssid: str = "11:22:33:44:55:66",
    channel: int | None = 6,
    security: str = "wpa2-psk",
) -> Frame:
    return Frame(
        type=FrameType.BEACON,
        ts=ts,
        ssid=ssid,
        bssid=bssid,
        channel=channel,
        security=security,
    )


# --- DeauthFloodDetector -----------------------------------------------------

def test_deauth_flood_fires_on_burst():
    d = DeauthFloodDetector(window_s=5.0, threshold=10)
    frames = [deauth(0.1 * i) for i in range(10)]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.kind is AlertKind.DEAUTH_FLOOD
    assert "flood" in a.reason.lower()
    assert a.severity == "critical"
    assert a.detail["count"] >= 10


def test_deauth_flood_quiet_on_benign_sparse():
    d = DeauthFloodDetector(window_s=5.0, threshold=20)
    # One deauth every 2s → at most 3 in a 5s window
    frames = [deauth(t) for t in (0.0, 2.0, 4.0, 6.0, 8.0)]
    assert d.feed_many(frames) == []


def test_deauth_flood_ignores_non_mgmt_kill():
    d = DeauthFloodDetector(window_s=5.0, threshold=5)
    frames = [beacon(0.1 * i) for i in range(20)]
    assert d.feed_many(frames) == []


def test_deauth_flood_counts_disassoc_too():
    d = DeauthFloodDetector(window_s=5.0, threshold=6)
    frames = [deauth(0.0), disassoc(0.1), deauth(0.2), disassoc(0.3), deauth(0.4), disassoc(0.5)]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert "disassoc" in alerts[0].reason.lower() or "flood" in alerts[0].reason.lower()


def test_alert_requires_reason():
    from redux.detect.alerts import Alert

    with pytest.raises(ValueError):
        Alert(kind=AlertKind.DEAUTH_FLOOD, reason="", ts=0.0)


# --- RogueAPDetector ---------------------------------------------------------

def test_rogue_ap_fires_on_unexpected_bssid():
    trusted = [
        TrustedNetwork(
            ssid="HomeNet",
            bssids=frozenset({"11:22:33:44:55:66"}),
            channels=frozenset({6}),
            security=frozenset({"wpa2-psk"}),
        )
    ]
    d = RogueAPDetector(trusted)
    # Same SSID, different BSSID → evil twin candidate
    a = d.feed(beacon(1.0, ssid="HomeNet", bssid="aa:bb:cc:dd:ee:ff", channel=6))
    assert a is not None
    assert a.kind is AlertKind.ROGUE_AP
    assert "BSSID" in a.reason
    assert a.ssid == "HomeNet"


def test_rogue_ap_fires_on_channel_mismatch():
    trusted = [
        TrustedNetwork(
            ssid="HomeNet",
            bssids=frozenset({"11:22:33:44:55:66"}),
            channels=frozenset({6}),
        )
    ]
    d = RogueAPDetector(trusted)
    a = d.feed(beacon(1.0, ssid="HomeNet", bssid="11:22:33:44:55:66", channel=11))
    assert a is not None
    assert "channel" in a.reason.lower()


def test_rogue_ap_fires_on_security_mismatch():
    trusted = [
        TrustedNetwork(
            ssid="HomeNet",
            bssids=frozenset({"11:22:33:44:55:66"}),
            security=frozenset({"wpa2-psk"}),
        )
    ]
    d = RogueAPDetector(trusted)
    a = d.feed(
        beacon(
            1.0,
            ssid="HomeNet",
            bssid="11:22:33:44:55:66",
            security="open",
        )
    )
    assert a is not None
    assert "security" in a.reason.lower()


def test_rogue_ap_quiet_when_matches_trusted():
    trusted = [
        TrustedNetwork(
            ssid="HomeNet",
            bssids=frozenset({"11:22:33:44:55:66"}),
            channels=frozenset({6}),
            security=frozenset({"wpa2-psk"}),
        )
    ]
    d = RogueAPDetector(trusted)
    assert d.feed(beacon(1.0)) is None


def test_rogue_ap_quiet_when_trusted_empty():
    d = RogueAPDetector([])  # nothing to compare — stay quiet
    assert d.feed(beacon(1.0, ssid="Anything", bssid="ff:ff:ff:ff:ff:ff")) is None


def test_rogue_ap_quiet_on_unknown_ssid():
    trusted = [
        TrustedNetwork(ssid="HomeNet", bssids=frozenset({"11:22:33:44:55:66"}))
    ]
    d = RogueAPDetector(trusted)
    assert d.feed(beacon(1.0, ssid="CoffeeShop", bssid="99:99:99:99:99:99")) is None


def test_rogue_ap_dedupes_repeat_sightings():
    trusted = [
        TrustedNetwork(ssid="HomeNet", bssids=frozenset({"11:22:33:44:55:66"}))
    ]
    d = RogueAPDetector(trusted)
    f = beacon(1.0, ssid="HomeNet", bssid="aa:bb:cc:dd:ee:ff")
    assert d.feed(f) is not None
    assert d.feed(f) is None  # same mismatch already reported


# --- BeaconSpamDetector ------------------------------------------------------

def test_beacon_spam_fires_on_many_unique_ssids():
    d = BeaconSpamDetector(window_s=10.0, unique_ssid_threshold=10, unique_bssid_threshold=100)
    frames = [
        beacon(0.1 * i, ssid=f"Fake-{i}", bssid=f"00:00:00:00:00:{i:02x}")
        for i in range(10)
    ]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.BEACON_SPAM
    assert "unique SSIDs" in alerts[0].reason


def test_beacon_spam_quiet_on_normal_ap():
    d = BeaconSpamDetector(window_s=10.0, unique_ssid_threshold=20, unique_bssid_threshold=30)
    # One real AP beaconing repeatedly
    frames = [beacon(0.5 * i, ssid="HomeNet", bssid="11:22:33:44:55:66") for i in range(40)]
    assert d.feed_many(frames) == []


def test_beacon_spam_ignores_non_beacon():
    d = BeaconSpamDetector(window_s=10.0, unique_ssid_threshold=5, unique_bssid_threshold=5)
    frames = [deauth(0.1 * i, bssid=f"00:00:00:00:00:{i:02x}") for i in range(20)]
    assert d.feed_many(frames) == []
