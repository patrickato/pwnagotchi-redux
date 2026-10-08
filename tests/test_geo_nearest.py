from redux.geo import Sighting, SightingStore
from redux.geo.nearest import nearest


def test_nearest_order():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=0.0, lon=0.0, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", lat=1.0, lon=0.0, ts=1.0, provenance="t"))
        hits = nearest(db, 0.0, 0.0, n=2)
        assert hits[0][0].mac == "aa:aa:aa:aa:aa:01"
        assert hits[0][1] < hits[1][1]
