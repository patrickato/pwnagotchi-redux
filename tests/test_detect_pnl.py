"""Hardware-free tests for PNL harvester (Grok D1)."""
from __future__ import annotations

from redux.detect.alerts import AlertKind
from redux.detect.frames import Frame, FrameType
from redux.detect.pnl import PNLHarvester


def probe(ts: float, sta: str, ssid: str) -> Frame:
    return Frame(type=FrameType.PROBE_REQ, ts=ts, src=sta, ssid=ssid)


def test_builds_pnl():
    h = PNLHarvester(loud_threshold=10)
    h.feed(probe(1.0, "aa:aa:aa:aa:aa:01", "Home"))
    h.feed(probe(2.0, "aa:aa:aa:aa:aa:01", "Work"))
    assert h.pnl_for("aa:aa:aa:aa:aa:01") == {"Home", "Work"}


def test_loud_prober_fires():
    h = PNLHarvester(loud_threshold=3)
    frames = [
        probe(float(i), "aa:aa:aa:aa:aa:01", f"Net-{i}") for i in range(3)
    ]
    alerts = h.feed_many(frames)
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.LOUD_PROBER
    assert "3 distinct" in alerts[0].reason


def test_quiet_below_threshold():
    h = PNLHarvester(loud_threshold=5)
    frames = [probe(float(i), "aa:aa:aa:aa:aa:01", f"N{i}") for i in range(3)]
    assert h.feed_many(frames) == []


def test_ignores_beacon():
    h = PNLHarvester(loud_threshold=1)
    f = Frame(type=FrameType.BEACON, ts=1.0, bssid="11:11:11:11:11:11", ssid="X")
    assert h.feed(f) is None


def test_dedupe_loud():
    h = PNLHarvester(loud_threshold=2)
    h.feed(probe(1.0, "aa:aa:aa:aa:aa:01", "A"))
    assert h.feed(probe(2.0, "aa:aa:aa:aa:aa:01", "B")) is not None
    assert h.feed(probe(3.0, "aa:aa:aa:aa:aa:01", "C")) is None
