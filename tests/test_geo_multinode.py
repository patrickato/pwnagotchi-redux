"""Hardware-free tests for multi-node merge (Grok GG9)."""
from redux.geo import Sighting, SightingStore
from redux.geo.multinode import merge_from_node, merge_stores, tag_node


def test_tag_node():
    s = Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="local")
    t = tag_node(s, "pi-north")
    assert "node=pi-north" in t.provenance


def test_merge_dedup_keeps_best_rssi():
    with SightingStore(":memory:") as db:
        merge_from_node(
            db,
            [Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-70, ts=1.0, provenance="a")],
            "node-a",
        )
        merge_from_node(
            db,
            [Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", rssi=-40, ts=2.0, provenance="b")],
            "node-b",
        )
        row = db.get("wifi", "aa:aa:aa:aa:aa:01")
        assert row is not None
        assert row.rssi == -40
        assert "node=" in row.provenance


def test_merge_stores():
    with SightingStore(":memory:") as src, SightingStore(":memory:") as dst:
        src.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", ts=1.0, provenance="x"))
        n = merge_stores(dst, src, "edge-1")
        assert n == 1
        assert dst.count() == 1
