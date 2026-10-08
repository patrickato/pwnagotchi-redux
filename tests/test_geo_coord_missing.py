from redux.geo import Sighting, SightingStore
from redux.geo.coord_missing import query_missing_coords, query_with_coords


def test_missing_and_present():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", lat=1.0, lon=2.0, ts=1.0, provenance="t"))
        assert len(query_missing_coords(db)) == 1
        assert len(query_with_coords(db)) == 1
