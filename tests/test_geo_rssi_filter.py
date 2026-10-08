from redux.geo import Sighting, SightingStore
from redux.geo.rssi_filter import query_by_rssi


def test_min_rssi():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-40, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", rssi=-90, ts=1.0, provenance="t"))
        strong = query_by_rssi(db, min_rssi=-50)
        assert len(strong) == 1 and strong[0].rssi == -40
