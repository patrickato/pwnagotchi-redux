from redux.geo import Sighting, SightingStore
from redux.geo.kind_filter import query_kinds


def test_kinds():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="ble", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        assert len(query_kinds(db, ["ble"])) == 1
        assert len(query_kinds(db, ["wifi", "ble"])) == 2
