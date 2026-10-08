from redux.geo import Sighting, SightingStore
from redux.geo.empty_ssid import count_empty_ssid, query_empty_ssid


def test_empty_ssid():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ssid="Home", ts=1.0, provenance="t"))
        assert len(query_empty_ssid(db)) == 1
        assert count_empty_ssid(db)["count"] == 1
