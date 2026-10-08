from redux.geo import Sighting, SightingStore
from redux.geo.empty_store import is_empty, is_nonempty


def test_empty():
    with SightingStore(":memory:") as db:
        assert is_empty(db)
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        assert is_nonempty(db)
