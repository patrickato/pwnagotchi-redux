from redux.geo import Sighting, SightingStore
from redux.geo.duplicate_ssid import multi_bssid_ssids


def test_multi_bssid():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="Twin", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ssid="Twin", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", ssid="Solo", ts=1.0, provenance="t"))
        hits = multi_bssid_ssids(db)
        assert len(hits) == 1 and hits[0]["ssid"] == "Twin"
