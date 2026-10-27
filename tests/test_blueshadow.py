"""Blue-shadow — the defensive footprint of each action + a quieter alternative."""
from redux.frameworks.blueshadow import shadow, all_shadows


def test_passive_action_is_silent():
    s = shadow("pmkid_capture")
    assert s["known"] is True and s["loud"] is False
    assert s["seen_by"] == [] and "silent" in s["footprint"]


def test_loud_action_names_the_detectors_and_offers_a_quieter_path():
    s = shadow("deauth")
    assert s["loud"] is True
    assert "deauth-flood" in s["seen_by"]              # what blue sees
    # it offers a stealthier way to the same goal
    assert s["stealthier"] is not None
    assert s["stealthier"]["action"] == "pmkid_capture"
    assert "silent" in s["stealthier"]["why"]


def test_shadow_carries_attack_and_defend_context():
    s = shadow("evil_twin")
    assert s["attack"] and s["defend"]                 # MITRE + D3FEND present
    assert s["loud"] is True and s["seen_by"]


def test_unknown_action_is_honest():
    s = shadow("nope")
    assert s["known"] is False and "no framework mapping" in s["reason"]


def test_all_shadows_sorted_loudest_first():
    rows = all_shadows()
    loud_counts = [len(r.get("seen_by", [])) for r in rows]
    assert loud_counts == sorted(loud_counts, reverse=True)   # loudest actions first
    assert any(r["loud"] for r in rows) and any(not r["loud"] for r in rows)
