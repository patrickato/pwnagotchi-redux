from redux.geo import Sighting, SightingStore
from redux.geo.store_diff import diff_stores


def test_diff():
    with SightingStore(":memory:") as a, SightingStore(":memory:") as b:
        a.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="t"))
        a.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        b.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ts=1.0, provenance="t"))
        b.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", ts=1.0, provenance="t"))
        d = diff_stores(a, b)
        assert len(d.only_left) == 1 and len(d.only_right) == 1 and len(d.both) == 1
