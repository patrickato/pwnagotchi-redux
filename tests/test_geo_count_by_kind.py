from redux.geo import Sighting, SightingStore
from redux.geo.count_by_kind import count_by_kind, count_by_kind_summary


def test_counts():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="ble", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", ts=1.0, provenance="t"))
        assert count_by_kind(db)["wifi"] == 2
        assert count_by_kind_summary(db)["total"] == 3
