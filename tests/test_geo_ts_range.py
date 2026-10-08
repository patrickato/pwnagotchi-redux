from redux.geo import Sighting, SightingStore
from redux.geo.ts_range import query_ts_range


def test_range():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=10.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=50.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", ts=100.0, provenance="t"))
        hits = query_ts_range(db, 20.0, 80.0)
        assert len(hits) == 1 and hits[0].mac.startswith("bb")
