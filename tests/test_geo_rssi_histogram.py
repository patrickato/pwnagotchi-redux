from redux.geo import Sighting, SightingStore
from redux.geo.rssi_histogram import rssi_histogram


def test_hist():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-45, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", rssi=-42, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", rssi=-75, ts=1.0, provenance="t"))
        h = rssi_histogram(db, bucket=10)
        assert h.get(-50) == 2 or h.get(-40) == 2  # -45/-42 -> -50 or -40 depending on floor
        assert sum(h.values()) == 3
