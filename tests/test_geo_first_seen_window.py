from redux.geo import Sighting, SightingStore
from redux.geo.first_seen_window import query_first_seen_window


def test_window():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=10.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=50.0, provenance="t"))
        hits = query_first_seen_window(db, 5.0, 20.0)
        assert len(hits) == 1
