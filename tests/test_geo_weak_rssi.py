from redux.geo import Sighting, SightingStore
from redux.geo.weak_rssi import query_weak_rssi


def test_weak():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-40, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", rssi=-90, ts=1.0, provenance="t"))
        weak = query_weak_rssi(db, -80)
        assert len(weak) == 1 and weak[0].rssi == -90
