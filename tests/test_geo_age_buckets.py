from redux.geo import Sighting, SightingStore
from redux.geo.age_buckets import age_buckets


def test_buckets():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1000.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        h = age_buckets(db, now_ts=2000.0)
        assert h["<1h"] == 1
        assert sum(h.values()) == 2
