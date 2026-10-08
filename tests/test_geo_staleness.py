from redux.geo import Sighting, SightingStore
from redux.geo.staleness import is_stale, query_fresh, query_stale


def test_fresh_and_stale():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=100.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=10.0, provenance="t"))
        fresh = query_fresh(db, now_ts=120.0, max_age_s=30.0)
        stale = query_stale(db, now_ts=120.0, max_age_s=30.0)
        assert len(fresh) == 1 and fresh[0].mac.endswith("01")
        assert len(stale) == 1
        assert is_stale(stale[0], 120.0, 30.0)
