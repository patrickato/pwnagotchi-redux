from redux.geo import Sighting, SightingStore
from redux.geo.latest_n import latest_n


def test_latest():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=9.0, provenance="t"))
        assert latest_n(db, 1)[0].mac.startswith("bb")
