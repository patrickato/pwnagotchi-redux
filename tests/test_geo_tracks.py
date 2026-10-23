"""Scope-bounded location trail — the one rule that keeps it from being mass
surveillance: a fix is logged ONLY for a target the central Scope authorizes.
An un-armed bystander is refused and nothing is written.
"""
from redux.geo.tracks import TrackStore, Fix
from redux.core import Scope


ARMED = "aa:bb:cc:dd:ee:ff"
BYSTANDER = "11:22:33:44:55:66"


def _scope(*bssids, job="hunt"):
    s = Scope()
    for b in bssids:
        s.add(b, "bssid", job=job)
    return s


# --- the Scope gate (the whole point) ---------------------------------------- #

def test_fix_for_an_armed_target_is_logged():
    t = TrackStore(":memory:")
    ok, why = t.record_fix(_scope(ARMED), ARMED, 42.1, -83.2, rssi=-55, now=100)
    assert ok is True and "logged fix for armed" in why
    assert t.count(ARMED) == 1


def test_fix_for_an_unarmed_bystander_is_refused_and_not_written():
    t = TrackStore(":memory:")
    ok, why = t.record_fix(_scope(ARMED), BYSTANDER, 42.1, -83.2, now=100)
    assert ok is False and "not logged" in why
    assert t.count() == 0            # nothing written for a target we never armed
    assert t.trail(BYSTANDER) == []


def test_empty_scope_logs_nothing():
    t = TrackStore(":memory:")
    ok, why = t.record_fix(Scope(), ARMED, 42.1, -83.2, now=100)
    assert ok is False and "scope is empty" in why
    assert t.count() == 0


def test_no_scope_refuses():
    t = TrackStore(":memory:")
    ok, why = t.record_fix(None, ARMED, 42.1, -83.2)
    assert ok is False and "no scope" in why


def test_expired_authorization_stops_logging():
    s = Scope()
    s.add(ARMED, "bssid", job="hunt", expires=150.0, now=100)
    t = TrackStore(":memory:")
    assert t.record_fix(s, ARMED, 42.1, -83.2, now=120)[0] is True   # still armed
    assert t.record_fix(s, ARMED, 42.1, -83.2, now=200)[0] is False  # lapsed → refused
    assert t.count(ARMED) == 1


# --- batch form gates each fix individually ---------------------------------- #

def test_record_many_logs_only_armed_targets():
    t = TrackStore(":memory:")
    fixes = [Fix(ARMED, 42.1, -83.2, ts=100, rssi=-50),
             Fix(BYSTANDER, 42.1, -83.2, ts=100),
             Fix(ARMED, 42.2, -83.3, ts=101, rssi=-60)]
    recorded, skipped = t.record_many(_scope(ARMED), fixes, now=100)
    assert recorded == 2
    assert any(BYSTANDER in s for s in skipped)
    assert t.count(ARMED) == 2 and t.count(BYSTANDER) == 0


# --- coordinate validation --------------------------------------------------- #

def test_out_of_range_or_nonnumeric_coordinates_are_refused():
    t = TrackStore(":memory:")
    s = _scope(ARMED)
    assert t.record_fix(s, ARMED, 999.0, 0.0, now=100)[0] is False
    assert t.record_fix(s, ARMED, "nope", 0.0, now=100)[0] is False
    assert t.count() == 0


# --- trail / read side ------------------------------------------------------- #

def test_trail_is_ordered_and_latest_is_newest():
    t = TrackStore(":memory:")
    s = _scope(ARMED)
    t.record_fix(s, ARMED, 42.0, -83.0, ts=100, now=100)
    t.record_fix(s, ARMED, 42.5, -83.5, ts=200, now=200)
    trail = t.trail(ARMED)
    assert [f["ts"] for f in trail] == [100.0, 200.0]
    assert t.latest(ARMED)["lat"] == 42.5


def test_bssid_is_normalized_lowercase():
    t = TrackStore(":memory:")
    s = _scope(ARMED)
    t.record_fix(s, "AA:BB:CC:DD:EE:FF", 42.0, -83.0, now=100)
    assert t.count(ARMED) == 1 and t.trail(ARMED)[0]["bssid"] == ARMED


def test_estimate_needs_two_rssi_tagged_fixes():
    t = TrackStore(":memory:")
    s = _scope(ARMED)
    t.record_fix(s, ARMED, 42.0, -83.0, rssi=-50, now=100)
    assert t.estimate(ARMED) is None                 # only one located sample
    t.record_fix(s, ARMED, 42.001, -83.001, rssi=-55, now=101)
    est = t.estimate(ARMED)
    assert est is not None and est.reason


def test_targets_and_stats():
    t = TrackStore(":memory:")
    s = _scope(ARMED, "00:00:00:00:00:01")
    t.record_fix(s, ARMED, 42.0, -83.0, now=100)
    t.record_fix(s, "00:00:00:00:00:01", 42.0, -83.0, now=101)
    assert set(t.targets()) == {ARMED, "00:00:00:00:00:01"}
    st = t.stats()
    assert st["fixes"] == 2 and st["targets"] == 2


# --- retention (mirror SightingStore.prune) ---------------------------------- #

def test_prune_by_age_and_max_rows():
    t = TrackStore(":memory:")
    s = _scope(ARMED)
    for i in range(5):
        t.record_fix(s, ARMED, 42.0, -83.0, ts=100 + i, now=100 + i)
    assert t.prune(older_than=2, now=105) >= 1        # drops the oldest
    assert t.prune(max_rows=1) >= 1                    # keep only the newest
    assert t.count(ARMED) == 1


def test_prune_rejects_invalid_inputs():
    t = TrackStore(":memory:")
    import pytest
    with pytest.raises(ValueError):
        t.prune(older_than=-1)
    with pytest.raises(ValueError):
        t.prune(max_rows=-1)


def test_forget_deletes_one_targets_trail():
    t = TrackStore(":memory:")
    s = _scope(ARMED, BYSTANDER)   # both armed here, then forget one
    t.record_fix(s, ARMED, 42.0, -83.0, now=100)
    t.record_fix(s, BYSTANDER, 42.0, -83.0, now=100)
    assert t.forget(ARMED) == 1
    assert t.count(ARMED) == 0 and t.count(BYSTANDER) == 1


# --- Augur integration: the live box re-gates fixes against its own Scope ----- #

def test_augur_record_hunt_fix_uses_the_live_scope():
    from redux.core import Augur
    from redux.radio import Radio, Intent
    r = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac")
    bc = Augur(radios=[r], intent=Intent.HUNT)
    bc._track_store = TrackStore(":memory:")     # in-memory for the test
    bc.scope = _scope(ARMED)
    ok, _ = bc.record_hunt_fix(ARMED, 42.0, -83.0, rssi=-55, now=100)
    assert ok is True
    bad, why = bc.record_hunt_fix(BYSTANDER, 42.0, -83.0, now=100)
    assert bad is False and "not logged" in why
    assert bc.track_store().count(ARMED) == 1
    assert bc.track_store().count(BYSTANDER) == 0
