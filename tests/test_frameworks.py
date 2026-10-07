"""Frameworks + purple Range mode — ATT&CK/D3FEND mapping and detector grading.

Pins the grading honesty: a MISS (attack ran, nothing caught it) is reported as a
gap, and NOT_APPLICABLE (passive/network-layer) is distinct from a miss and never
counts against coverage.
"""
import pytest

from redux.frameworks import (
    REGISTRY, actions, get, attack_defend_pairs,
    Verdict, run_exercise, range_report,
)


# --- registry ---------------------------------------------------------------- #

def test_known_actions_have_attack_ids_and_phase():
    for a in ("deauth", "evil_twin", "handshake_capture", "captive_portal"):
        m = get(a)
        assert m.attack_ids and m.ptes_phase
    assert get("deauth").attack_ids == ("T1498",)
    assert "T1557" in get("evil_twin").attack_ids


def test_unknown_action_raises():
    with pytest.raises(KeyError):
        get("nope")


def test_pairs_cover_every_registry_action():
    assert {p["action"] for p in attack_defend_pairs()} == set(actions())


# --- grading verdicts -------------------------------------------------------- #

def test_detected_when_all_expected_fire():
    r = run_exercise("deauth", ["deauth-flood", "surveillance-sweep"])
    assert r.verdict is Verdict.DETECTED
    assert r.attack_ids == ["T1498"] and r.defend == ["D3-NTA"]


def test_partial_when_some_fire():
    r = run_exercise("evil_twin", ["rogue-AP"])     # pineapple, karma missing
    assert r.verdict is Verdict.PARTIAL
    assert "karma" in r.reason and "pineapple" in r.reason


def test_missed_is_a_named_gap():
    r = run_exercise("captive_portal", [])          # nothing fired
    assert r.verdict is Verdict.MISSED
    assert "GAP" in r.reason


def test_not_applicable_for_passive_technique():
    r = run_exercise("pmkid_capture", [])           # no detector expected
    assert r.verdict is Verdict.NOT_APPLICABLE


def test_detector_match_is_case_insensitive():
    r = run_exercise("evil_twin", ["ROGUE-ap", "Pineapple", "karma"])
    assert r.verdict is Verdict.DETECTED


# --- report aggregation ------------------------------------------------------ #

def test_report_coverage_excludes_not_applicable_and_lists_gaps():
    results = [
        run_exercise("deauth", ["deauth-flood", "surveillance-sweep"]),  # detected
        run_exercise("handshake_capture", ["handshake"]),                # detected
        run_exercise("evil_twin", ["rogue-AP"]),                         # partial
        run_exercise("captive_portal", []),                              # missed (gap)
        run_exercise("pmkid_capture", []),                               # n/a
    ]
    rep = range_report(results)
    assert rep["applicable"] == 4                 # the n/a one is excluded
    assert rep["fully_detected"] == 2
    assert rep["coverage"] == 0.5
    assert len(rep["gaps"]) == 1 and rep["gaps"][0]["action"] == "captive_portal"
    assert rep["counts"]["n/a"] == 1


def test_report_handles_all_not_applicable():
    rep = range_report([run_exercise("wifi_recon", []), run_exercise("net_scan", [])])
    assert rep["applicable"] == 0 and rep["coverage"] is None
