from redux.geo import Sighting, SightingStore
from redux.geo.copy_store import copy_store


def test_copy():
    with SightingStore(":memory:") as src, SightingStore(":memory:") as dst:
        src.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        r = copy_store(src, dst)
        assert r["copied"] == 1 and dst.count() == 1
