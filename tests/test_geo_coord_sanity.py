from redux.geo import Sighting, SightingStore
from redux.geo.coord_sanity import find_bad_coords


def test_bad_lat():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", lat=-122.0, lon=37.0, ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", lat=37.0, lon=-122.0, ts=1.0, provenance="t"))
        bad = find_bad_coords(db)
        assert len(bad) == 1
        assert bad[0]["mac"].startswith("aa")
