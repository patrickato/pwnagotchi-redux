from redux.geo import Sighting, SightingStore
from redux.geo.ssid_search import search_ssid, ssids_matching


def test_substring():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="CafeWiFi", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ssid="Home", ts=1.0, provenance="t"))
        hits = search_ssid(db, "cafe")
        assert len(hits) == 1
        assert ssids_matching(db, "WiFi") == ["CafeWiFi"]
