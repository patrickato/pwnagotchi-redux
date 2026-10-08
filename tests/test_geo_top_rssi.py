from redux.geo import Sighting, SightingStore
from redux.geo.top_rssi import top_rssi


def test_top():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-90, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", rssi=-30, ts=1.0, provenance="t"))
        top = top_rssi(db, 1)
        assert top[0].rssi == -30
