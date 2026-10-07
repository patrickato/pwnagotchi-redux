"""Sentinel mode — deploy-and-watch guardian.

Pins dispatch/threshold, windowed de-dup, and the armed-vs-home rule for CSI
motion (home motion is suppressed, not fired), plus the Beastcore wiring.
"""
import math

from redux.sentinel import Sentinel, Severity, CollectingNotifier, CallableNotifier
from redux.sense.csi import SenseReading, Sense, CsiFrame
from redux.core import Beastcore
from redux.radio import Intent


class _Alert:
    """Duck-typed stand-in for a detector Alert (Sentinel never imports the lane)."""
    def __init__(self, kind, reason, ts, severity="warning", bssid="", ssid=""):
        self.kind = kind; self.reason = reason; self.ts = ts
        self.severity = severity; self.bssid = bssid; self.ssid = ssid


# --- dispatch + threshold ---------------------------------------------------- #

def test_dispatches_at_or_above_min_severity():
    note = CollectingNotifier()
    s = Sentinel(notifier=note, min_severity=Severity.WARN)
    assert s.observe_alert(_Alert("rogue_ap", "twin", 1.0, "warning", bssid="a"), now=1.0) is not None
    assert len(note.sent) == 1


def test_below_threshold_is_suppressed():
    s = Sentinel(min_severity=Severity.CRITICAL)
    assert s.observe_alert(_Alert("loud_prober", "noisy", 1.0, "warning", bssid="a"), now=1.0) is None
    assert s.suppressed == 1


# --- de-dup ------------------------------------------------------------------ #

def test_repeat_within_window_is_collapsed_then_counted():
    s = Sentinel(dedup_window=60.0)
    s.observe_alert(_Alert("deauth_flood", "burst", 0.0, "warning", bssid="x"), now=0.0)
    assert s.observe_alert(_Alert("deauth_flood", "burst", 5.0, "warning", bssid="x"), now=5.0) is None
    # after the window, it re-fires carrying the collapsed repeat count
    ev = s.observe_alert(_Alert("deauth_flood", "burst", 90.0, "warning", bssid="x"), now=90.0)
    assert ev is not None and ev.repeat == 2


def test_distinct_signatures_are_not_deduped():
    s = Sentinel()
    a = s.observe_alert(_Alert("rogue_ap", "r", 0.0, "warning", bssid="a"), now=0.0)
    b = s.observe_alert(_Alert("rogue_ap", "r", 1.0, "warning", bssid="b"), now=1.0)
    assert a is not None and b is not None


# --- CSI motion: armed vs home ----------------------------------------------- #

def _motion():
    return SenseReading(Sense.MOTION, 9.9, 0.001, 42.0, "42sigma above baseline")


def test_motion_while_armed_is_critical():
    s = Sentinel(armed=True)
    ev = s.observe_motion(_motion(), now=10.0)
    assert ev is not None and ev.severity is Severity.CRITICAL


def test_motion_at_home_is_suppressed_not_fired():
    s = Sentinel(armed=False)
    assert s.observe_motion(_motion(), now=10.0) is None
    assert s.suppressed == 1


def test_unknown_and_still_never_alert():
    s = Sentinel(armed=True)
    assert s.observe_motion(SenseReading(Sense.UNKNOWN, 0, 0, 0, "warming"), now=1.0) is None
    assert s.observe_motion(SenseReading(Sense.STILL, 0.1, 0.1, 0.5, "quiet"), now=2.0) is None


def test_tracker_is_critical():
    s = Sentinel(armed=True)
    ev = s.observe_tracker("11:22:33:44:55:66", label="AirTag", now=5.0)
    assert ev is not None and ev.severity is Severity.CRITICAL


# --- notifier + status ------------------------------------------------------- #

def test_callable_notifier_receives_dicts():
    got = []
    s = Sentinel(notifier=CallableNotifier(got.append))
    s.observe_alert(_Alert("ble_skimmer", "skimmer", 1.0, "critical", bssid="z"), now=1.0)
    assert got and got[0]["severity"] == "critical" and got[0]["source"] == "detector:ble_skimmer"


def test_status_counts_by_severity():
    s = Sentinel()
    s.observe_alert(_Alert("rogue_ap", "r", 0.0, "warning", bssid="a"), now=0.0)
    s.observe_alert(_Alert("ble_skimmer", "s", 1.0, "critical", bssid="b"), now=1.0)
    st = s.status()
    assert st["dispatched"] == 2 and st["by_severity"]["critical"] == 1 and st["by_severity"]["warn"] == 1


# --- Beastcore wiring -------------------------------------------------------- #

def _frames(kind, n, t0=0):
    def jit(t, j): return 0.08 * (((t * 2654435761 + j * 40503) % 1000) / 1000.0 - 0.5)
    out = []
    for t in range(t0, t0 + n):
        if kind == "quiet":
            amp = tuple(10.0 + jit(t, j) for j in range(32))
        else:
            amp = tuple(10.0 + 3.0 * math.sin(0.8 * t + 0.5 * j) + jit(t, j) for j in range(32))
        out.append(CsiFrame(ts=float(t), amp=amp))
    return out


def test_beastcore_observe_csi_routes_motion_to_armed_sentinel():
    bc = Beastcore(radios=None, intent=Intent.RECON)
    eng = bc.enable_sense(window=16, sensitivity=5.0)
    eng.calibrate(_frames("quiet", 80))
    bc.enable_sentinel(armed=True)
    # warm the window, then feed motion frames
    for f in _frames("quiet", 20, t0=200):
        bc.observe_csi(f)
    for f in _frames("motion", 30, t0=300):
        bc.observe_csi(f)
    assert bc.sentinel_status()["dispatched"] >= 1
