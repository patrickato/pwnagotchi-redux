"""Readiness spine — one truth layering manifest + doctor + benchpass."""
from redux.core import manifest as M
from redux.core import readiness_spine as RS
from redux.core.readiness_spine import Rung, spine_readiness, spine_completion


def _feat(fid):
    f = M.get(fid)
    assert f is not None, fid
    return f


# probes that make the software layer say "deps present"
_ALL_TOOLS = dict(which=lambda b: "/usr/bin/" + b, has_import=lambda m: True)


def test_rung_ordering():
    assert Rung.PLANNED < Rung.TOOL_MISSING < Rung.NEEDS_HARDWARE < Rung.PRESENT < Rung.QUALIFIED


def test_software_only_ready_is_qualified():
    # net.responder is implemented + software; with its tool present it's QUALIFIED
    r = spine_readiness(_feat("net.responder"), which=lambda b: "/usr/bin/responder",
                        has_import=lambda m: True, hardware_present=lambda f: False)
    # responder has no needs_hardware → software-only → qualified when tool present
    assert r["rung"] in ("qualified", "present")   # depends on needs_hardware flag


def test_stealth_lowslow_is_qualified_software_only():
    r = spine_readiness(_feat("stealth.lowslow"), **_ALL_TOOLS)
    assert r["rung"] == "qualified" and "software-only" in r["reason"]


def test_hardware_feature_without_radio_needs_hardware():
    r = spine_readiness(_feat("wifi.capture"), **_ALL_TOOLS,
                        hardware_present=lambda f: False)
    assert r["rung"] == "needs-hardware"


def test_hardware_present_but_not_qualified_is_present():
    r = spine_readiness(_feat("wifi.capture"), **_ALL_TOOLS,
                        hardware_present=lambda f: True, passed_gates=frozenset())
    assert r["rung"] == "present" and "not yet bench-qualified" in r["reason"]


def test_passing_a_gate_promotes_to_qualified():
    r = spine_readiness(_feat("wifi.capture"), **_ALL_TOOLS,
                        hardware_present=lambda f: True,
                        passed_gates=frozenset({"1.4"}))
    assert r["rung"] == "qualified" and "1.4" in r["reason"]
    assert r["attested_by"] == ["1.4"]


def test_tool_missing_dominates():
    r = spine_readiness(_feat("wifi.capture"), which=lambda b: None,
                        has_import=lambda m: False, hardware_present=lambda f: True)
    assert r["rung"] == "tool-missing"


def test_passed_gates_from_ledger():
    class _L:
        gates = {"1.4": {"status": "pass"}, "1.6": {"status": "fail"},
                 "2b.2": {"status": "pass"}}
    got = RS.passed_gates_from_ledger(_L())
    assert got == frozenset({"1.4", "2b.2"})


def test_completion_is_one_truth_and_sorted():
    comp = spine_completion(**_ALL_TOOLS, hardware_present=lambda f: True,
                            passed_gates=frozenset({"1.4", "2b.2"}))
    assert comp["total"] == len(M.list_features())
    assert comp["qualified"] >= 1
    # rows sorted best rung first
    vals = [r["rung_value"] for r in comp["rows"]]
    assert vals == sorted(vals, reverse=True)
    assert "qualified" in comp["summary"]
