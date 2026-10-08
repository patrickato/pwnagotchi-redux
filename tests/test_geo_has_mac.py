from redux.geo import Sighting, SightingStore
from redux.geo.has_mac import has_mac, has_mac_any_kind


def test_has_mac():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        assert has_mac(db, "aa:aa:aa:aa:aa:01") is True
        assert has_mac(db, "ff:ff:ff:ff:ff:ff") is False
        assert has_mac_any_kind(db, "AA:AA:AA:AA:AA:01") is True
