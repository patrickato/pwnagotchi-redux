from redux.geo import Sighting, SightingStore
from redux.geo.delta_sync import sync_since


def test_sync_since():
    with SightingStore(":memory:") as src, SightingStore(":memory:") as dst:
        src.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=10.0, provenance="t"))
        src.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=50.0, provenance="t"))
        r = sync_since(src, dst, 20.0)
        assert r["imported"] == 1
        assert dst.count() == 1
