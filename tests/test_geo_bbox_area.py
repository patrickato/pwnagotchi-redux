from redux.geo import Sighting, SightingStore
from redux.geo.bbox_area import bbox_area_km2, store_bbox_area_km2


def test_area_positive():
    a = bbox_area_km2(0.0, 0.0, 1.0, 1.0)
    assert a > 0


def test_store_area():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=0.0, lon=0.0, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", lat=0.1, lon=0.1, ts=1.0, provenance="t"))
        r = store_bbox_area_km2(db)
        assert r is not None and r[0] > 0 and "km" in r[1]
