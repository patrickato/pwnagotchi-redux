"""Lab mode — the own-lab switch that approves firing on sight (opt-in, logged)."""
from __future__ import annotations

from redux.core.scope import Scope
from redux.engine import BettercapDriver, BettercapConfig, Allowlist, FiringRefused, ReplayTransport


# --- Scope.authorize --------------------------------------------------------- #

def test_default_scope_still_fails_closed():
    s = Scope()
    assert s.lab_mode is False
    ok, why = s.authorize(bssid="aa:bb:cc:dd:ee:ff")
    assert ok is False and "empty" in why and "lab mode" in why  # refusal hints at the switch


def test_lab_mode_approves_concrete_rf_target_on_sight():
    s = Scope(lab_mode=True)
    ok, why = s.authorize(bssid="aa:bb:cc:dd:ee:ff")
    assert ok is True and "lab mode" in why and "aa:bb:cc:dd:ee:ff" in why
    ok, why = s.authorize(ssid="HomeNet")
    assert ok is True and "lab mode" in why


def test_lab_mode_still_refuses_a_targetless_call():
    s = Scope(lab_mode=True)
    ok, why = s.authorize()  # nothing to aim at
    assert ok is False


def test_explicit_arm_reports_the_entry_not_lab_mode():
    s = Scope(lab_mode=True)
    s.add("aa:bb:cc:dd:ee:ff", "bssid", label="my AP")
    ok, why = s.authorize(bssid="aa:bb:cc:dd:ee:ff")
    assert ok is True and "bssid aa:bb:cc:dd:ee:ff" in why  # the armed entry wins over lab-mode text


def test_lab_mode_network_private_yes_public_no():
    s = Scope(lab_mode=True)
    ok, why = s.authorize_network("192.168.1.0/24")
    assert ok is True and "private" in why
    ok, why = s.authorize_network("8.8.8.0/24")
    assert ok is False and "public" in why  # the one guard that stays even in lab mode


def test_lab_mode_persists_across_save_load(tmp_path):
    p = tmp_path / "scope.json"
    s = Scope(lab_mode=True)
    s.add("aa:bb:cc:dd:ee:ff", "bssid")
    s.save(str(p))
    reloaded = Scope.load(str(p))
    assert reloaded.lab_mode is True
    assert reloaded.authorize(bssid="11:22:33:44:55:66")[0] is True  # unarmed target approved in lab mode


def test_summary_reports_lab_mode():
    assert Scope().summary()["lab_mode"] is False
    assert Scope(lab_mode=True).summary()["lab_mode"] is True


# --- driver firing gate ------------------------------------------------------ #

def test_driver_lab_mode_allows_unarmed_target_but_not_garbage():
    drv = BettercapDriver(transport=ReplayTransport(), allowlist=Allowlist(lab_mode=True))
    # an un-armed, well-formed BSSID fires in lab mode
    drv.deauth("aa:bb:cc:dd:ee:ff")
    # a malformed BSSID is still refused (injection guard is not loosened)
    import pytest
    with pytest.raises(FiringRefused):
        drv.deauth("not-a-mac")


def test_arm_from_scope_propagates_lab_mode():
    s = Scope(lab_mode=True)   # empty scope, lab mode on
    drv = BettercapDriver(transport=ReplayTransport(), allowlist=Allowlist())
    drv.arm_from_scope(s)
    assert drv.allowlist.lab_mode is True
    assert drv.allowlist.empty is True              # nothing armed...
    drv.deauth("aa:bb:cc:dd:ee:ff")                 # ...yet lab mode lets it fire


def test_driver_without_lab_mode_still_fails_closed():
    import pytest
    drv = BettercapDriver(transport=ReplayTransport(), allowlist=Allowlist())
    with pytest.raises(FiringRefused):
        drv.deauth("aa:bb:cc:dd:ee:ff")
