"""Resource ownership / lease broker — arbitrate physical radios without clobbering."""
from redux.core.leases import (
    ResourceBroker, Resource, Lease, EXCLUSIVE, SHARED,
)


def _broker(now=1000.0):
    clock = {"t": now}
    b = ResourceBroker(clock=lambda: clock["t"])
    b.register(Resource("wlan1", "wifi"))
    b.register(Resource("wlan0", "wifi"))
    b.register(Resource("gps0", "gps", shareable=True))
    return b, clock


def test_acquire_free_resource():
    b, _ = _broker()
    g = b.acquire("wlan1", "bettercap", reason="capture")
    assert g.ok and g.lease.owner == "bettercap"
    assert b.owner_of("wlan1") == "bettercap"


def test_unknown_resource_is_refused():
    b, _ = _broker()
    g = b.acquire("wlan9", "x")
    assert not g.ok and "no such resource" in g.reason


def test_exclusive_conflict_same_priority_denied():
    b, _ = _broker()
    b.acquire("wlan1", "bettercap", priority=5, reason="capture")
    g = b.acquire("wlan1", "angryoxide", priority=5, reason="scalpel")
    assert not g.ok and "held by" in g.reason
    assert b.owner_of("wlan1") == "bettercap"       # incumbent keeps it


def test_higher_priority_preempts_lower():
    b, _ = _broker()
    b.acquire("wlan1", "bettercap", priority=1, reason="baseline capture")
    g = b.acquire("wlan1", "angryoxide", priority=9, reason="preferred scalpel")
    assert g.ok and g.preempted and g.preempted[0].owner == "bettercap"
    assert b.owner_of("wlan1") == "angryoxide"
    snap = b.snapshot()
    assert snap["recent_revocations"][-1]["evicted"] == "bettercap"


def test_lower_priority_cannot_preempt():
    b, _ = _broker()
    b.acquire("wlan1", "angryoxide", priority=9)
    g = b.acquire("wlan1", "bettercap", priority=1)
    assert not g.ok and b.owner_of("wlan1") == "angryoxide"


def test_shared_resource_allows_multiple_readers():
    b, _ = _broker()
    g1 = b.acquire("gps0", "gpsd", mode=SHARED, reason="position")
    g2 = b.acquire("gps0", "fox-hunt", mode=SHARED, reason="read fix")
    assert g1.ok and g2.ok
    assert len(b.holders("gps0")) == 2


def test_multiband_coexist_on_distinct_radios():
    b, _ = _broker()
    assert b.acquire("wlan1", "capture-5g", reason="5GHz").ok
    assert b.acquire("wlan0", "sense-csi", reason="onboard CSI").ok
    # distinct resources → both held, no conflict
    assert b.owner_of("wlan1") == "capture-5g" and b.owner_of("wlan0") == "sense-csi"


def test_release_frees_the_resource():
    b, _ = _broker()
    b.acquire("wlan1", "bettercap")
    assert b.release("wlan1", "bettercap") is True
    assert b.owner_of("wlan1") is None
    assert b.acquire("wlan1", "angryoxide").ok      # now free


def test_ttl_lease_lapses_so_a_crashed_owner_doesnt_wedge_it():
    b, clock = _broker()
    b.acquire("wlan1", "flaky-engine", ttl=30.0, reason="might crash")
    assert b.owner_of("wlan1") == "flaky-engine"
    clock["t"] += 31.0                               # time passes, owner never renewed
    assert b.owner_of("wlan1") is None               # lease lapsed
    assert b.acquire("wlan1", "bettercap").ok        # resource reclaimable


def test_revoke_is_audited():
    b, _ = _broker()
    b.acquire("wlan1", "hung-engine")
    assert b.revoke("wlan1", "hung-engine", reason="unresponsive") is True
    snap = b.snapshot()
    assert snap["recent_revocations"][-1]["reason"] == "unresponsive"
    assert snap["leased"] == 0


def test_renew_replaces_own_lease_not_duplicates():
    b, _ = _broker()
    b.acquire("wlan1", "bettercap", reason="v1")
    b.acquire("wlan1", "bettercap", reason="v2")     # renew
    assert len(b.holders("wlan1")) == 1 and b.holders("wlan1")[0].reason == "v2"


def test_snapshot_reports_map():
    b, _ = _broker()
    b.acquire("wlan1", "bettercap", reason="capture")
    snap = b.snapshot()
    assert snap["leased"] == 1 and snap["free"] == 2
    row = next(r for r in snap["resources"] if r["resource"] == "wlan1")
    assert row["holders"][0]["owner"] == "bettercap" and row["free"] is False
