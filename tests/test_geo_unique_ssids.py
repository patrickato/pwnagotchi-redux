from redux.geo import Sighting, SightingStore
from redux.geo.unique_ssids import unique_ssids


def test_unique():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="A", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ssid="A", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", ssid="B", ts=1.0, provenance="t"))
        assert unique_ssids(db) == ["A", "B"]
