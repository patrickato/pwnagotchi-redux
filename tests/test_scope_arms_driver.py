"""The central Scope arms the bettercap driver's deauth gate.

Before this wiring the driver's firing allowlist was disjoint from the Scope, so
`scope add` never armed deauth and it always fail-closed. These tests pin the
behaviour: only ACTIVE BSSID/SSID scope entries arm deauth, CIDRs don't, an empty
or de-scoped Scope refuses, and the Augur scope setter keeps the driver in sync.
"""
import pytest

from redux.core.augur import Augur
from redux.core.scope import Scope
from redux.engine import BettercapDriver, ReplayTransport, FiringRefused
from redux.engine.bettercap_driver import BettercapConfig, Allowlist


def _driver():
    return BettercapDriver(config=BettercapConfig(), transport=ReplayTransport(events=[]))


# --- driver.arm_from_scope (unit) ------------------------------------------ #

def test_arm_from_scope_bssid_and_ssid():
    s = Scope()
    s.add("AA:BB:CC:DD:EE:FF", label="ap")
    s.add("MyLab", "ssid")
    al = _driver().arm_from_scope(s)
    assert "aa:bb:cc:dd:ee:ff" in al.bssids
    assert "MyLab" in al.ssids


def test_arm_from_scope_skips_cidr():
    s = Scope()
    s.add("192.168.1.0/24")
    al = _driver().arm_from_scope(s)
    assert al.empty is True            # a CIDR is network-layer, not a deauth target


def test_arm_from_scope_empty_is_failclosed():
    al = _driver().arm_from_scope(Scope())
    assert al.empty is True


# --- end-to-end through Augur (the real wiring) ---------------------------- #

def test_scope_setter_arms_then_disarms_deauth():
    aug = Augur(driver=_driver(), scope=Scope())
    tgt = "AA:BB:CC:DD:EE:FF"

    # empty scope → refuse
    with pytest.raises(FiringRefused):
        aug.supervisor.driver.deauth(tgt)

    # arm it → fires
    s = Scope(); s.add(tgt, label="ap")
    aug.scope = s
    assert aug.supervisor.driver.deauth(tgt)            # no raise

    # a different, un-armed target still refuses
    with pytest.raises(FiringRefused):
        aug.supervisor.driver.deauth("11:22:33:44:55:66")

    # de-scope → disarmed again
    s.remove(tgt)
    aug.scope = s
    with pytest.raises(FiringRefused):
        aug.supervisor.driver.deauth(tgt)


def test_expired_scope_entry_does_not_arm():
    aug = Augur(driver=_driver(), scope=Scope())
    s = Scope()
    s.add("AA:BB:CC:DD:EE:FF", expires=1001.0, now=1000.0)  # absolute expiry in the past
    # active_entries() defaults now=time.time(), far past 1001, so the entry is
    # already expired and must not arm the driver.
    aug.scope = s
    with pytest.raises(FiringRefused):
        aug.supervisor.driver.deauth("AA:BB:CC:DD:EE:FF")
