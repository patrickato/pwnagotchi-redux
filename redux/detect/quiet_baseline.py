"""Quiet-baseline helper — feed benign frames, assert no alerts.

Used by tests and operators to confirm detectors stay silent on normal traffic.
"""
from __future__ import annotations

from typing import Iterable, List, Protocol

from redux.detect.alerts import Alert
from redux.detect.frames import Frame, FrameType


class _Det(Protocol):
    def feed(self, frame: Frame):
        ...


def benign_home_frames(start_ts: float = 1.0) -> List[Frame]:
    """A short, quiet home-lab sequence (single AP, sparse probes)."""
    return [
        Frame(type=FrameType.BEACON, ts=start_ts, bssid="aa:aa:aa:aa:aa:01", ssid="Home", channel=6, security="wpa2"),
        Frame(type=FrameType.BEACON, ts=start_ts + 1.0, bssid="aa:aa:aa:aa:aa:01", ssid="Home", channel=6, security="wpa2"),
        Frame(type=FrameType.PROBE_REQ, ts=start_ts + 2.0, src="bb:bb:bb:bb:bb:01", ssid="Home"),
        Frame(type=FrameType.PROBE_RESP, ts=start_ts + 2.1, bssid="aa:aa:aa:aa:aa:01", ssid="Home", src="aa:aa:aa:aa:aa:01"),
    ]


def collect_alerts(detectors: Iterable[_Det], frames: Iterable[Frame]) -> List[Alert]:
    out: List[Alert] = []
    for fr in frames:
        for d in detectors:
            a = d.feed(fr)
            if a is not None:
                out.append(a)
    return out
