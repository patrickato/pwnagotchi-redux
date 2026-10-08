from redux.geo import Sighting, SightingStore
from redux.geo.radio_source import counts_by_radio, query_radio


def test_by_radio():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", source_radio="wlan0mon", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", source_radio="wlan1mon", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", source_radio="wlan0mon", ts=1.0, provenance="t"))
        assert counts_by_radio(db)["wlan0mon"] == 2
        assert len(query_radio(db, "wlan1mon")) == 1
