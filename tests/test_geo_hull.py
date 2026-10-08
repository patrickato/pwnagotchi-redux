from redux.geo import Sighting, SightingStore
from redux.geo.hull import bounding_box, convex_hull, store_bounding_box


def test_bbox():
    bb = bounding_box([(0.0, 0.0), (1.0, 2.0), (0.5, 0.5)])
    assert bb is not None
    assert bb.min_lat == 0.0 and bb.max_lon == 2.0


def test_store_bbox():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=1.0, lon=2.0, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", lat=3.0, lon=4.0, ts=1.0, provenance="t"))
        bb = store_bounding_box(db)
        assert bb is not None and bb.max_lat == 3.0


def test_hull_square():
    pts = [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5)]
    h = convex_hull(pts)
    assert len(h) == 4
    assert (0.5, 0.5) not in h
