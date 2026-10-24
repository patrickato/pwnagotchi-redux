"""Lab-mode active capture (auto-offense).

The always-on pump is PASSIVE by default and only transmits deauth when the
scope's lab_mode is on (your own gear). It nudges each seen AP with a
rate-limited deauth to force a 4-way, and skips any AP retired after capture.
"""
import types

from redux.core import Augur
from redux.radio import Radio, Intent
from redux.core.scope import Scope
from redux.engine import normalize_event
from redux.geo import SightingStore

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
             driver="mt76x2u", usb_gen=3, high_draw=True)


class FakeDriver:
    def __init__(self, events=()):
        self._e = list(events)
        self.deauthed = []        # (bssid, ssid) calls recorded
        self.refuse = False
    def set_interface(self, iface): pass
    def recon(self, on=True): pass
    def set_channels(self, channels): pass
    def arm_from_scope(self, scope): pass
    def poll_events(self, clear=True):
        e, self._e = list(self._e), []   # drained once, like the real engine
        return e
    def deauth(self, bssid, ssid=None):
        if self.refuse:
            raise RuntimeError("REFUSED deauth (simulated)")
        self.deauthed.append((bssid, ssid))
        return {}


def _agent(lab, events=()):
    return Augur([ONBOARD, ALFA], intent=Intent.RECON, driver=FakeDriver(events),
                 scope=Scope(lab_mode=lab), store=SightingStore(":memory:"))


def test_offense_note_collects_ap_bssids():
    a = _agent(lab=True)
    ev = normalize_event({"tag": "wifi.ap.new", "time": 1.0,
                          "data": {"mac": "aa:bb:cc:dd:ee:09", "essid": "dad24"}})
    a._offense_note(types.SimpleNamespace(payload={"event": ev}))
    assert a._offense_known.get("aa:bb:cc:dd:ee:09") == "dad24"


def test_offense_deauths_known_aps_in_lab_mode():
    a = _agent(lab=True)
    a._offense_known = {"aa:bb:cc:dd:ee:01": "dad24", "aa:bb:cc:dd:ee:02": ""}
    assert a._drive_offense() == 2
    assert {b for b, _ in a.supervisor.driver.deauthed} == {
        "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}


def test_offense_silent_when_lab_mode_off():
    a = _agent(lab=False)
    a._offense_known = {"aa:bb:cc:dd:ee:01": "x"}
    assert a._drive_offense() == 0
    assert a.supervisor.driver.deauthed == []


def test_offense_rate_limits_per_ap():
    a = _agent(lab=True)
    clock = [1000.0]
    a._offense_clock = lambda: clock[0]
    a._offense_known = {"aa:bb:cc:dd:ee:01": "x"}
    assert a._drive_offense() == 1      # first nudge
    assert a._drive_offense() == 0      # too soon — rate-limited
    clock[0] += a._offense_interval + 1
    assert a._drive_offense() == 1      # after the interval, nudge again


def test_retired_ap_is_skipped():
    a = _agent(lab=True)
    a._offense_known = {"aa:bb:cc:dd:ee:01": "x", "aa:bb:cc:dd:ee:02": "y"}
    a.retire_ap("aa:bb:cc:dd:ee:01")
    a._drive_offense()
    assert [b for b, _ in a.supervisor.driver.deauthed] == ["aa:bb:cc:dd:ee:02"]


def test_firing_refusal_never_crashes_the_pump():
    a = _agent(lab=True)
    a.supervisor.driver.refuse = True
    a._offense_known = {"aa:bb:cc:dd:ee:01": "x"}
    assert a._drive_offense() == 0                      # refused -> nothing counted
    assert "aa:bb:cc:dd:ee:01" in a._offense_last       # but it backed off, no hammering


def test_pump_deauths_seen_aps_in_lab_mode():
    evs = [normalize_event({"tag": "wifi.ap.new", "time": 1.0,
                            "data": {"mac": "aa:bb:cc:dd:ee:07", "essid": "dad24", "channel": 6}})]
    a = _agent(lab=True, events=evs)
    a.pump()
    assert ("aa:bb:cc:dd:ee:07", "dad24") in a.supervisor.driver.deauthed


def test_pump_is_fully_passive_when_lab_mode_off():
    evs = [normalize_event({"tag": "wifi.ap.new", "time": 1.0,
                            "data": {"mac": "aa:bb:cc:dd:ee:08", "essid": "x"}})]
    a = _agent(lab=False, events=evs)
    a.pump()
    assert a.supervisor.driver.deauthed == []
