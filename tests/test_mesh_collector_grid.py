"""Hardware-free tests for the collector-grid cross-node RSSI fusion."""
from __future__ import annotations

from redux.mesh import CollectorGrid


def test_fuse_requires_distinct_nodes_fails_closed():
    grid = CollectorGrid()
    grid.add_observation("alpha", 37.0, -122.0, "AA:BB:CC:DD:EE:01", -50)
    # only one node so far
    est, reason = grid.fuse("aa:bb:cc:dd:ee:01", min_nodes=2)
    assert est is None and "need 2 distinct" in reason
    # a repeat from the SAME node doesn't count as a second node
    grid.add_observation("alpha", 37.0005, -122.0, "AA:BB:CC:DD:EE:01", -55)
    est, reason = grid.fuse("aa:bb:cc:dd:ee:01", min_nodes=2)
    assert est is None


def test_fuse_across_nodes_produces_estimate_between_them():
    grid = CollectorGrid()
    # three nodes around a target; the strongest (closest) is alpha
    grid.add_observation("alpha", 37.0000, -122.0000, "aa:bb:cc:dd:ee:01", -40)
    grid.add_observation("bravo", 37.0100, -122.0000, "aa:bb:cc:dd:ee:01", -70)
    grid.add_observation("charlie", 37.0000, -122.0100, "aa:bb:cc:dd:ee:01", -70)
    est, reason = grid.fuse("aa:bb:cc:dd:ee:01", min_nodes=2)
    assert est is not None
    assert est.observation_count == 3
    # weighted toward alpha (strongest): latitude close to 37.0, lon close to -122.0
    assert 37.0 <= est.lat < 37.006
    assert -122.006 < est.lon <= -122.0
    assert "fused 3 collector nodes" in reason and "alpha" in reason


def test_keeps_strongest_per_node_not_chatty_repeats():
    grid = CollectorGrid()
    grid.add_observation("alpha", 37.0, -122.0, "aa:bb:cc:dd:ee:01", -80)
    grid.add_observation("alpha", 37.5, -122.5, "aa:bb:cc:dd:ee:01", -40)  # stronger, kept
    grid.add_observation("bravo", 38.0, -122.0, "aa:bb:cc:dd:ee:01", -75)
    obs = grid.observations("aa:bb:cc:dd:ee:01")
    alpha = [o for o in obs if o.node_id == "alpha"][0]
    assert alpha.rssi == -40 and alpha.node_lat == 37.5  # strongest sample retained
    assert len(obs) == 2  # one per node


def test_targets_and_nodes_enumerated():
    grid = CollectorGrid()
    grid.add_observation("alpha", 37.0, -122.0, "aa:bb:cc:dd:ee:01", -50)
    grid.add_observation("bravo", 37.0, -122.0, "aa:bb:cc:dd:ee:02", -50)
    assert grid.targets() == ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]
    assert grid.nodes() == ["alpha", "bravo"]


def test_fuse_all_reports_each_target_with_reason():
    grid = CollectorGrid()
    grid.add_observation("alpha", 37.0, -122.0, "aa:bb:cc:dd:ee:01", -50)
    grid.add_observation("bravo", 37.01, -122.0, "aa:bb:cc:dd:ee:01", -60)
    grid.add_observation("alpha", 37.0, -122.0, "aa:bb:cc:dd:ee:02", -50)  # only 1 node
    out = grid.fuse_all(min_nodes=2)
    assert out["aa:bb:cc:dd:ee:01"][0] is not None
    assert out["aa:bb:cc:dd:ee:02"][0] is None and "need 2 distinct" in out["aa:bb:cc:dd:ee:02"][1]


def test_mac_normalization_merges_case_variants():
    grid = CollectorGrid()
    grid.add_observation("alpha", 37.0, -122.0, "AA:BB:CC:DD:EE:01", -50)
    grid.add_observation("bravo", 37.01, -122.0, "aa:bb:cc:dd:ee:01", -60)
    est, _ = grid.fuse("Aa:Bb:Cc:Dd:Ee:01", min_nodes=2)
    assert est is not None and est.observation_count == 2
