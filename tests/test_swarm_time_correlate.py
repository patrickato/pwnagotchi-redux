"""Swarm time-correlation — corroboration, movement, merged timeline across nodes."""
from redux.mesh.time_correlate import SwarmTimeCorrelator, SwarmEvent, CorrelatedEvent


def _ev(node, ts, entity="aa:bb:cc:dd:ee:ff", kind="deauth", rssi=-50):
    return SwarmEvent(node_id=node, ts=ts, entity=entity, kind=kind, rssi=rssi)


def test_corroboration_needs_distinct_nodes_in_window():
    c = SwarmTimeCorrelator(window=5.0)
    c.add(_ev("alpha", 100.0))
    c.add(_ev("beta", 102.0))          # same entity+kind, 2s later, different node
    corr = c.corroborated()
    assert len(corr) == 1
    assert corr[0].nodes == ("alpha", "beta") and corr[0].confidence == 1.0


def test_one_node_repeating_is_not_corroboration():
    c = SwarmTimeCorrelator(window=5.0)
    c.add(_ev("alpha", 100.0))
    c.add(_ev("alpha", 101.0))         # same node twice → not cross-node
    assert c.corroborated() == []


def test_events_outside_window_do_not_correlate():
    c = SwarmTimeCorrelator(window=5.0)
    c.add(_ev("alpha", 100.0))
    c.add(_ev("beta", 200.0))          # 100s apart → separate
    assert c.corroborated() == []


def test_clock_skew_is_normalized():
    # beta's clock runs 50s ahead; after correcting skew the two line up
    c = SwarmTimeCorrelator(window=5.0)
    c.set_skew("beta", 50.0)
    c.add(_ev("alpha", 100.0))
    c.add(_ev("beta", 152.0))          # raw 52s apart, but -50 skew → 2s apart
    corr = c.corroborated()
    assert len(corr) == 1 and set(corr[0].nodes) == {"alpha", "beta"}


def test_movement_hops_between_nodes():
    c = SwarmTimeCorrelator()
    c.add(_ev("alpha", 100.0, kind="probe"))
    c.add(_ev("beta", 110.0, kind="probe"))
    c.add(_ev("gamma", 125.0, kind="probe"))
    hops = c.movements("aa:bb:cc:dd:ee:ff")
    assert [(h.from_node, h.to_node) for h in hops] == [("alpha", "beta"), ("beta", "gamma")]
    assert hops[0].dt == 10.0 and hops[1].dt == 15.0


def test_no_hop_when_same_node_repeats():
    c = SwarmTimeCorrelator()
    c.add(_ev("alpha", 100.0))
    c.add(_ev("alpha", 110.0))
    assert c.movements("aa:bb:cc:dd:ee:ff") == []


def test_confidence_scales_with_node_count():
    c = SwarmTimeCorrelator(window=10.0)
    for i, node in enumerate(("a", "b", "c", "d")):
        c.add(_ev(node, 100.0 + i))     # 4 nodes, 4 total nodes → confidence 1.0
    corr = c.corroborated()
    assert corr[0].confidence == 1.0 and len(corr[0].nodes) == 4


def test_timeline_merges_on_common_clock():
    c = SwarmTimeCorrelator()
    c.set_skew("beta", 50.0)
    c.add(_ev("beta", 152.0, kind="beacon"))
    c.add(_ev("alpha", 100.0, kind="beacon"))
    tl = c.timeline("aa:bb:cc:dd:ee:ff")
    assert [r["t"] for r in tl] == [100.0, 102.0]        # sorted on adjusted clock
    assert [r["node"] for r in tl] == ["alpha", "beta"]


def test_summary_counts():
    c = SwarmTimeCorrelator(window=5.0)
    c.add(_ev("alpha", 100.0))
    c.add(_ev("beta", 101.0))
    c.add(_ev("gamma", 300.0, entity="11:22:33:44:55:66"))
    s = c.summary()
    assert s["nodes"] == 3 and s["events"] == 3 and s["entities"] == 2
    assert s["corroborated"] == 1
