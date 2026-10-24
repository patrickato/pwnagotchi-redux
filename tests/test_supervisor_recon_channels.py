"""Optional recon channel narrowing — open sweep by default, narrow by choice."""
from __future__ import annotations

from redux.radio import Radio, Intent
from redux.core import Supervisor

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
             driver="mt76x2u", usb_gen=3)


class ChanDriver:
    def __init__(self):
        self.calls = []
        self.recon_on = None

    def set_interface(self, iface):
        self.calls.append(("set_interface", iface))

    def recon(self, on=True):
        self.recon_on = on
        self.calls.append(("recon", on))

    def set_channels(self, channels):
        self.calls.append(("set_channels", list(channels)))

    def poll_events(self, clear=True):
        return []


def test_default_is_full_sweep_no_set_channels():
    drv = ChanDriver()
    Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, driver=drv)   # no recon_channels
    assert drv.recon_on is True
    assert not any(c[0] == "set_channels" for c in drv.calls)      # nothing narrowed


def test_operator_chosen_channels_narrow_the_sweep():
    drv = ChanDriver()
    Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, driver=drv,
               recon_channels=[1, 6, 11, 36])
    assert drv.recon_on is True
    # recon is started first, THEN narrowed — order matters
    assert drv.calls.index(("recon", True)) < drv.calls.index(("set_channels", [1, 6, 11, 36]))


def test_empty_list_is_treated_as_full_sweep():
    drv = ChanDriver()
    Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, driver=drv, recon_channels=[])
    assert not any(c[0] == "set_channels" for c in drv.calls)


def test_augur_passes_recon_channels_through():
    from redux.core import Augur
    drv = ChanDriver()
    Augur(radios=[ONBOARD, ALFA], intent=Intent.HUNT, driver=drv,
          recon_channels=[36, 149])
    assert ("set_channels", [36, 149]) in drv.calls


def test_driver_without_set_channels_is_tolerated():
    # a driver that doesn't support narrowing must not crash the supervisor
    class NoChan:
        def __init__(self): self.recon_on = None
        def set_interface(self, iface): pass
        def recon(self, on=True): self.recon_on = on
        def poll_events(self, clear=True): return []
    drv = NoChan()
    Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, driver=drv, recon_channels=[1, 6])
    assert drv.recon_on is True   # recon still started; narrowing silently skipped
