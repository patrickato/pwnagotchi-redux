from redux.geo import Sighting, SightingStore
from redux.geo.minmax_ts import minmax_ts


def test_minmax():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=10.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=50.0, provenance="t"))
        assert minmax_ts(db) == (10.0, 50.0)
