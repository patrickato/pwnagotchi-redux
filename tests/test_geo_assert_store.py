from redux.geo import Sighting, SightingStore
from redux.geo.assert_store import assert_count, assert_has, assert_missing


def test_asserts():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        assert_count(db, 1)
        assert_has(db, "wifi", "aa:aa:aa:aa:aa:01")
        assert_missing(db, "wifi", "ff:ff:ff:ff:ff:ff")
