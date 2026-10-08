from redux.geo import Sighting, SightingStore
from redux.geo.mac_prefix import query_mac_prefix


def test_prefix():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:bb:cc:01:02:03", ts=1.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="ff:ff:ff:01:02:03", ts=1.0, provenance="t"))
        hits = query_mac_prefix(db, "aa:bb:cc")
        assert len(hits) == 1
