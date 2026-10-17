"""Offline phone-management policies: no Bluetooth stack or radio device needed."""
import math
import pytest

from redux.connectivity.phone import (
    PhoneSession, PhonePhase, PhoneIntent, PhonePolicy,
)


def paired_session():
    phone = PhoneSession()
    assert phone.enable(now=0) == (PhoneIntent.ENABLE_ADAPTER,)
    assert phone.open_pairing(owner_authorized=True, now=1) == (
        PhoneIntent.START_DISCOVERABLE,)
    assert phone.pairing_result(authenticated=True, trusted=True, now=10) == (
        PhoneIntent.STOP_DISCOVERABLE,)
    return phone


def test_disabled_by_default_no_discoverability_or_reconnect():
    p = PhoneSession()
    assert p.phase is PhonePhase.DISABLED
    assert not p.status(now=0)["discoverable"]
    assert p.tick(now=9999) == ()
    assert p.status(now=9999)["trusted_bond_verified"] is False


def test_owner_must_explicitly_trigger_short_pairing_window():
    p = PhoneSession()
    assert p.enable(now=0) == (PhoneIntent.ENABLE_ADAPTER,)
    with pytest.raises(PermissionError):
        p.open_pairing(owner_authorized=False, now=1)
    assert p.phase is PhonePhase.IDLE
    assert p.open_pairing(owner_authorized=True, now=1) == (
        PhoneIntent.START_DISCOVERABLE,)
    assert p.status(now=2)["pairing_seconds_left"] == 89
    assert p.status(now=2)["discoverable"] is True
    assert p.tick(now=91) == (PhoneIntent.STOP_DISCOVERABLE,)
    assert p.tick(now=92) == ()
    assert p.phase is PhonePhase.IDLE
    assert p.status(now=92)["discoverable"] is False


def test_untrusted_and_unauthenticated_bonds_never_connect():
    for authenticated, trusted in ((False, True), (True, False), (False, False)):
        p = PhoneSession()
        p.enable(now=1)
        p.open_pairing(owner_authorized=True, now=2)
        p.pairing_result(authenticated=authenticated, trusted=trusted, now=3)
        assert p.status(now=3)["trusted_bond_verified"] is False
        with pytest.raises(PermissionError):
            p.link_connected(authenticated=True, trusted=True, now=4)


def test_authenticated_pairing_allows_only_authenticated_bonded_connection():
    p = paired_session()
    with pytest.raises(PermissionError):
        p.link_connected(authenticated=False, trusted=True, now=11)
    with pytest.raises(PermissionError):
        p.link_connected(authenticated=True, trusted=False, now=12)
    assert p.link_connected(authenticated=True, trusted=True, now=13) == ()
    assert p.status(now=13)["phase"] == "connected"


def test_pairing_expiration_is_hard_cutoff_even_without_tick():
    p = PhoneSession(PhonePolicy(pairing_window_s=15))
    p.enable(now=1)
    p.open_pairing(owner_authorized=True, now=2)
    assert p.pairing_result(authenticated=True, trusted=True, now=17) == (
        PhoneIntent.STOP_DISCOVERABLE,)
    assert p.status(now=17)["trusted_bond_verified"] is False


def test_reconnect_uses_bounded_backoff_without_public_discovery():
    p = paired_session()
    p.link_connected(authenticated=True, trusted=True, now=11)
    p.link_lost(now=12)
    assert p.status(now=12)["phase"] == "backoff"
    assert p.status(now=12)["retry_seconds_left"] == 5
    assert p.tick(now=16) == ()
    assert p.tick(now=17) == (PhoneIntent.CONNECT_TRUSTED,)
    assert p.tick(now=18) == ()
    p.connect_failed(now=18)
    assert p.status(now=18)["retry_seconds_left"] == 10
    assert p.tick(now=28) == (PhoneIntent.CONNECT_TRUSTED,)
    p.connect_failed(now=28)
    assert p.status(now=28)["retry_seconds_left"] == 20
    assert p.status(now=28)["discoverable"] is False
    with pytest.raises(PermissionError):
        p.link_connected(authenticated=True, trusted=True, now=29)
    assert p.tick(now=48) == (PhoneIntent.CONNECT_TRUSTED,)
    assert p.link_connected(authenticated=True, trusted=True, now=49) == ()
    assert p.status(now=49)["last_failure_count"] == 0


def test_retry_capped_and_not_repeated_inside_single_tick():
    p = paired_session()
    p.link_connected(authenticated=True, trusted=True, now=11)
    p.link_lost(now=12)
    current = 12
    for _ in range(11):
        current += 400
        assert p.tick(now=current) == (PhoneIntent.CONNECT_TRUSTED,)
        assert p.tick(now=current) == ()
        p.connect_failed(now=current)
    assert p.status(now=current)["retry_seconds_left"] == 300


def test_disable_revokes_trust_and_closes_pairing():
    p = paired_session()
    assert p.disable(now=12) == (PhoneIntent.DISABLE_ADAPTER,)
    assert p.status(now=13)["trusted_bond_verified"] is False
    assert p.tick(now=1000) == ()
    assert p.enable(now=1001) == (PhoneIntent.ENABLE_ADAPTER,)
    with pytest.raises(PermissionError):
        p.link_connected(authenticated=True, trusted=True, now=1002)


def test_disabling_during_pairing_stops_discoverability_first():
    p = PhoneSession()
    p.enable(now=1)
    p.open_pairing(owner_authorized=True, now=2)
    assert p.disable(now=3) == (
        PhoneIntent.STOP_DISCOVERABLE, PhoneIntent.DISABLE_ADAPTER)


def test_disabling_connected_phone_disconnects_before_poweroff():
    p = paired_session()
    p.link_connected(authenticated=True, trusted=True, now=11)
    assert p.disable(now=12) == (
        PhoneIntent.DISCONNECT, PhoneIntent.DISABLE_ADAPTER)


@pytest.mark.parametrize("now", [
    -1, True, None, "12", math.inf, math.nan, -math.inf,
    1e308, 10 ** 2000, 1_000_000_000_001,
])
def test_invalid_clocks_fail_closed(now):
    p = PhoneSession()
    with pytest.raises(ValueError):
        p.enable(now=now)


def test_extreme_clocks_cannot_extend_discoverability_forever():
    p = PhoneSession()
    p.enable(now=1)
    with pytest.raises(ValueError):
        p.open_pairing(owner_authorized=True, now=1e308)
    assert p.phase is PhonePhase.IDLE
    assert p.status(now=2)["discoverable"] is False


def test_monotonic_clock_rollbacks_are_not_accepted():
    p = PhoneSession()
    p.enable(now=20)
    with pytest.raises(ValueError):
        p.tick(now=19)


@pytest.mark.parametrize("bad", [
    {"pairing_window_s": 0},
    {"pairing_window_s": 301},
    {"pairing_window_s": True},
    {"initial_backoff_s": 0},
    {"maximum_backoff_s": 0},
    {"maximum_backoff_s": 3601},
    {"maximum_backoff_s": True},
])
def test_policy_bounds(bad):
    with pytest.raises(ValueError):
        PhonePolicy(**bad)


def test_pairing_refuses_when_connected_or_disabled():
    p = PhoneSession()
    with pytest.raises(RuntimeError):
        p.open_pairing(owner_authorized=True, now=1)
    p.enable(now=2)
    p.open_pairing(owner_authorized=True, now=3)
    with pytest.raises(RuntimeError):
        p.open_pairing(owner_authorized=True, now=4)
    p.pairing_result(authenticated=True, trusted=True, now=5)
    p.link_connected(authenticated=True, trusted=True, now=6)
    with pytest.raises(RuntimeError):
        p.open_pairing(owner_authorized=True, now=7)


def test_invalid_transition_cannot_initiate_retry_or_bond():
    p = PhoneSession()
    p.enable(now=1)
    with pytest.raises(RuntimeError):
        p.connect_failed(now=2)
    with pytest.raises(RuntimeError):
        p.pairing_result(authenticated=True, trusted=True, now=3)
    assert p.status(now=3)["phase"] == "idle"


def test_status_contains_no_credentials_or_identifiers():
    p = paired_session()
    expected = {
        "phase", "discoverable", "trusted_bond_verified",
        "pairing_seconds_left", "retry_seconds_left", "last_failure_count",
    }
    assert set(p.status(now=10)) == expected
    assert "wlan" not in str(p.status(now=10))
