from redux.geo import Sighting, SightingStore
from redux.geo.clear_kind import clear_kind


def test_clear_kind():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="ble", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        r = clear_kind(db, "wifi")
        assert r["deleted"] == 1
        assert db.count() == 1
