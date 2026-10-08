from redux.geo import Sighting, SightingStore
from redux.geo.summary import store_summary, store_summary_text


def test_summary():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="X", lat=1.0, lon=2.0, rssi=-50, ts=1.0, provenance="t"))
        s = store_summary(db)
        assert s["total"] == 1 and s["with_coords"] == 1
        assert "store summary" in store_summary_text(db)
