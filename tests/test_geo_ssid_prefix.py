from redux.geo import Sighting, SightingStore
from redux.geo.ssid_prefix import query_ssid_prefix


def test_prefix():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="HomeGuest", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ssid="Cafe", ts=1.0, provenance="t"))
        hits = query_ssid_prefix(db, "home")
        assert len(hits) == 1 and hits[0].ssid == "HomeGuest"
