"""Hardware-free tests for WPS-attack / PIN-bruteforce detector (Grok #4)."""
from __future__ import annotations

from redux.detect import AlertKind, Frame, FrameType, WPSAttackDetector


def wps(
    ts: float,
    *,
    bssid: str = "11:22:33:44:55:66",
    src: str = "aa:bb:cc:dd:ee:01",
    opcode: str = "m1",
    ssid: str = "HomeNet",
) -> Frame:
    return Frame(
        type=FrameType.WPS,
        ts=ts,
        bssid=bssid,
        src=src,
        ssid=ssid,
        wps_opcode=opcode,
    )


def test_wps_fires_on_attempt_flood():
    d = WPSAttackDetector(window_s=30.0, attempt_threshold=10, nack_threshold=100)
    frames = [wps(0.1 * i, opcode="m1") for i in range(10)]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.WPS_ATTACK
    assert "WPS" in alerts[0].reason
    assert alerts[0].detail["attempts"] >= 10


def test_wps_fires_on_nack_flood():
    d = WPSAttackDetector(window_s=30.0, attempt_threshold=100, nack_threshold=5)
    frames = [wps(0.1 * i, opcode="nack") for i in range(5)]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert "NACK" in alerts[0].reason


def test_wps_quiet_on_sparse_legit_enrollee():
    d = WPSAttackDetector(window_s=30.0, attempt_threshold=12, nack_threshold=6)
    # Slow M1/M2 handshake — normal enrollee
    frames = [
        wps(0.0, opcode="m1"),
        wps(1.0, opcode="m2"),
        wps(2.0, opcode="m3"),
        wps(3.0, opcode="m4"),
    ]
    assert d.feed_many(frames) == []


def test_wps_ignores_non_wps_frames():
    d = WPSAttackDetector(window_s=30.0, attempt_threshold=3, nack_threshold=3)
    frames = [
        Frame(type=FrameType.BEACON, ts=0.1 * i, bssid="11:22:33:44:55:66", ssid="X")
        for i in range(20)
    ]
    assert d.feed_many(frames) == []


def test_wps_dedupes_per_bssid():
    d = WPSAttackDetector(window_s=30.0, attempt_threshold=5, nack_threshold=100)
    frames = [wps(0.05 * i) for i in range(5)]
    assert len(d.feed_many(frames)) == 1
    # Continued attack should not re-alert until reset
    more = [wps(10.0 + 0.05 * i) for i in range(5)]
    assert d.feed_many(more) == []


def test_wps_tracks_distinct_sources_in_reason():
    d = WPSAttackDetector(window_s=30.0, attempt_threshold=6, nack_threshold=100)
    frames = [
        wps(0.1 * i, src=f"aa:bb:cc:dd:ee:{i:02x}", opcode="m1") for i in range(6)
    ]
    alerts = d.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].detail["sources"] == 6
    assert "source" in alerts[0].reason.lower()
