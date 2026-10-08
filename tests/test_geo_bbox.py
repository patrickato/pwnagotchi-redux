from redux.geo import Sighting, SightingStore
from redux.geo.bbox import in_bbox, query_bbox


def test_in_bbox():
    assert in_bbox(0.5, 0.5, (0.0, 0.0, 1.0, 1.0))
    assert not in_bbox(2.0, 0.5, (0.0, 0.0, 1.0, 1.0))


def test_query_bbox():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=0.5, lon=0.5, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", lat=5.0, lon=5.0, ts=1.0, provenance="t"))
        hits = query_bbox(db, (0.0, 0.0, 1.0, 1.0))
        assert len(hits) == 1
        assert hits[0].mac == "aa:aa:aa:aa:aa:01"
