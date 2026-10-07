from redux.radio import Radio, Intent, Role
from redux.core import Supervisor
from redux.core.supervisor import capture_iface

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
             driver="mt76x2u", usb_gen=3, high_draw=True)


class FakeDriver:
    def __init__(self, events=None):
        self.iface = None
        self.recon_on = None
        self.calls = []
        self._events = events or []

    def set_interface(self, iface):
        self.iface = iface
        self.calls.append(("set_interface", iface))

    def recon(self, on=True):
        self.recon_on = on
        self.calls.append(("recon", on))

    def poll_events(self, clear=True):
        return list(self._events)


class FakeRadioControl:
    def __init__(self):
        self.applied = []

    def apply(self, assignment):
        self.applied.append(assignment)
        return []


def test_hunt_points_driver_at_injector_and_starts_recon():
    drv = FakeDriver()
    ctl = FakeRadioControl()
    sup = Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, radio_control=ctl, driver=drv)
    # Alfa (injector, 5GHz) is the capture radio in hunt
    assert sup.capture_iface == "wlan1"
    assert drv.iface == "wlan1"
    assert drv.recon_on is True
    assert len(ctl.applied) == 1          # radio layer was told to apply the assignment


def test_online_has_no_capture_radio_so_recon_off():
    drv = FakeDriver()
    sup = Supervisor([ONBOARD], intent=Intent.ONLINE, driver=drv)
    assert sup.capture_iface is None
    assert drv.recon_on is False          # nothing to capture -> engine idle


def test_intent_switch_repoints():
    drv = FakeDriver()
    sup = Supervisor([ONBOARD, ALFA], intent=Intent.ONLINE, driver=drv)
    sup.set_intent(Intent.HUNT)
    assert sup.capture_iface == "wlan1"
    assert ("set_interface", "wlan1") in drv.calls


def test_hotplug_add_then_remove_reassigns_and_falls_back():
    drv = FakeDriver()
    sup = Supervisor([ONBOARD], intent=Intent.HUNT, driver=drv)
    first = sup.capture_iface               # only onboard -> it captures (passive/degraded)
    sup.add_radio(ALFA)
    assert sup.capture_iface == "wlan1"     # promote the injector
    sup.remove_radio("wlan1")
    assert sup.capture_iface == first       # graceful fallback to onboard


def test_tick_drains_driver_events():
    drv = FakeDriver(events=[{"tag": "wifi.client.handshake"}])
    sup = Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, driver=drv)
    assert sup.tick() == [{"tag": "wifi.client.handshake"}]


def test_works_with_no_collaborators_and_logs_reasons():
    rec = []
    sup = Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, log=rec.append)
    # pure decision mode: assignment computed, glass-box reasons captured, no crash
    assert sup.assignment is not None
    assert sup.reasons and rec            # reasons narrated to the log
    assert sup.tick() == []               # no driver -> nothing to drain


def test_capture_iface_helper():
    sup = Supervisor([ONBOARD, ALFA], intent=Intent.HUNT)
    assert capture_iface(sup.assignment) == "wlan1"
    assert capture_iface(None) is None
