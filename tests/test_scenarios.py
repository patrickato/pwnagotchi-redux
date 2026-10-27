"""Scenario library — playbooks that tie the wave together + manifest readiness."""
from redux.core import scenarios as S
from redux.core import manifest as M


def test_every_scenario_step_names_a_real_capability():
    # the whole point of the map: no step references a capability the manifest
    # doesn't actually have.
    for scen in S.list_scenarios():
        for step in scen.steps:
            assert M.get(step.capability) is not None, f"{scen.name}: unknown {step.capability}"


def test_core_scenarios_present():
    names = {s.name for s in S.list_scenarios()}
    assert {"silent-loot", "lan-pivot", "ghost-recon", "clean-exit"} <= names


def test_get_is_case_insensitive():
    assert S.get("Silent-Loot").name == "silent-loot"
    assert S.get("nope") is None


def test_validate_reports_runnable_when_all_ready():
    # force every probe to "present" → all implemented steps are READY
    scen = S.get("clean-exit")               # software-only steps (scrub)
    rep = S.validate(scen, which=lambda b: "/usr/bin/" + b, has_import=lambda m: True,
                     hardware_present=lambda f: True)
    assert rep["runnable"] is True and rep["blocked"] == 0 and rep["unknown"] == 0


def test_validate_flags_hardware_blocked_steps():
    # no hardware present → a radio-needing step is blocked, scenario not runnable
    scen = S.get("silent-loot")
    rep = S.validate(scen, which=lambda b: "/usr/bin/" + b, has_import=lambda m: True,
                     hardware_present=lambda f: False)
    assert rep["runnable"] is False and rep["blocked"] >= 1
    assert any(r["state"] in ("needs-hardware", "tool-missing") for r in rep["steps"])


def test_validate_catches_an_unknown_capability():
    bogus = S.Scenario(name="x", goal="g", teaches="t", posture="silent",
                       steps=(S.Step("bad", "does.not.exist", "redux x", "why"),))
    rep = S.validate(bogus)
    assert rep["unknown"] == 1 and rep["runnable"] is False


def test_capabilities_helper_lists_step_features():
    scen = S.get("lan-pivot")
    assert "net.responder" in scen.capabilities and "wifi.capture" in scen.capabilities
