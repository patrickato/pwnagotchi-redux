from redux.geo import Sighting, SightingStore
from redux.geo.count_summary import count_summary


def test_summary():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="X", lat=1.0, lon=2.0, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="ble", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        s = count_summary(db)
        assert s["total"] == 2 and s["with_coords"] == 1 and s["with_ssid"] == 1
