from redux.geo import Sighting, SightingStore
from redux.geo.ssid_count import top_ssids


def test_top_ssids():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="Hot", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ssid="Hot", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", ssid="Cold", ts=1.0, provenance="t"))
        assert top_ssids(db, 1)[0] == ("Hot", 2)
