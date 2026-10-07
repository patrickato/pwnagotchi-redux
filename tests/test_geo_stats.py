"""Hardware-free tests for store stats (Grok G7)."""
from redux.geo import Sighting, SightingStore
from redux.geo.stats import densest_cells, new_since, summary, top_ssids


def test_top_ssids_and_summary():
    with SightingStore(":memory:") as db:
        db.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ssid="A", lat=1.0, lon=2.0, ts=10.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:01", ssid="A", lat=1.0, lon=2.0, ts=20.0, provenance="t"))
        db.insert(Sighting(kind="wifi", mac="cc:cc:cc:cc:cc:01", ssid="B", lat=1.1, lon=2.1, ts=30.0, provenance="t"))
        tops = top_ssids(db)
        assert tops[0][0] == "A" and tops[0][1] == 2
        assert new_since(db, 15.0) == 2
        assert densest_cells(db)
        s = summary(db)
        assert s["total"] == 3


def test_cell_key_floors_across_zero():
    # regression: int() truncates toward zero, merging the two hemispheres in the
    # zero-straddling cell. floor keeps them in distinct cells.
    from redux.geo.stats import _cell_key
    assert _cell_key(0.009, 0.0, 0.01) != _cell_key(-0.009, 0.0, 0.01)
