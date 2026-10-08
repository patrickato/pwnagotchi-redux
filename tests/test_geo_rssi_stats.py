from redux.geo import Sighting, SightingStore
from redux.geo.rssi_stats import rssi_stats


def test_rssi_stats():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-40, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", rssi=-60, ts=1.0, provenance="t"))
        s = rssi_stats(db)
        assert s.count == 2 and s.min_rssi == -60 and s.max_rssi == -40
        assert s.mean_rssi == -50.0
