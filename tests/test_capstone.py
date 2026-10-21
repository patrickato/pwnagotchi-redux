"""Capstone — the one hands-off pass (Phase-1 gate 1.6). Pure/hardware-free:
intent -> role assignment -> repoint -> posture -> bounded capture plan, with a
glass-box reason on every step and no transmission unless posture + Scope allow.
"""
from redux.capstone import plan_capstone, sample_inventory, Capstone
from redux.radio.orchestrator import Radio, Role, Intent
from redux.core.scope import Scope


def _onboard():
    return Radio(iface="wlan0", bands=frozenset({"2.4"}), monitor=False,
                 inject=False, onboard=True)


def _alfa():
    return Radio(iface="wlan1", bands=frozenset({"2.4", "5"}), monitor=True,
                 inject=True, onboard=False, usb_gen=2)


def test_recon_is_passive_and_assigns_a_capture_radio():
    cap = plan_capstone(sample_inventory(), "recon", Scope(), offense_enabled=False)
    assert isinstance(cap, Capstone)
    assert cap.ok is True
    assert cap.capture_iface == "wlan1"           # the monitor-capable adapter
    assert cap.posture == "passive" and cap.transmit is False
    steps = {s.step for s in cap.steps}
    assert {"inventory", "orchestrate", "capture_radio", "repoint",
            "posture", "capture"} <= steps


def test_every_step_carries_a_glassbox_reason():
    cap = plan_capstone(sample_inventory(), "recon", Scope())
    assert cap.steps
    for s in cap.steps:
        assert s.reason.strip(), f"step {s.step!r} has no reason"


def test_hunt_with_offense_but_empty_scope_stays_passive():
    # offense enabled, but nothing armed -> nothing to aim at -> no transmit
    cap = plan_capstone(sample_inventory(), "hunt", Scope(), offense_enabled=True)
    assert cap.posture == "passive" and cap.transmit is False
    assert cap.armed_targets == 0


def test_hunt_with_offense_and_armed_target_goes_active():
    scope = Scope()
    scope.add("00:11:22:33:44:55", job="lab")
    cap = plan_capstone(sample_inventory(), "hunt", scope, offense_enabled=True)
    assert cap.posture == "active" and cap.transmit is True
    assert cap.armed_targets == 1


def test_armed_target_without_offense_stays_passive():
    scope = Scope()
    scope.add("00:11:22:33:44:55", job="lab")
    cap = plan_capstone(sample_inventory(), "hunt", scope, offense_enabled=False)
    assert cap.posture == "passive" and cap.transmit is False


def test_no_radios_stops_honestly():
    cap = plan_capstone([], "recon", Scope())
    assert cap.ok is False
    assert cap.capture_iface is None
    assert cap.steps[0].step == "inventory" and cap.steps[0].ok is False


def test_no_monitor_capable_radio_means_no_capture():
    # a lone managed-only onboard radio can't take the capture role
    cap = plan_capstone([_onboard()], "recon", Scope())
    assert cap.capture_iface is None
    assert cap.ok is False


def test_missing_engine_still_proves_the_arrange_step():
    cap = plan_capstone(sample_inventory(), "recon", Scope(), engine_available=False)
    assert cap.ok is False                         # can't capture without an engine
    assert cap.capture_iface == "wlan1"            # but radios were arranged
    repoint = next(s for s in cap.steps if s.step == "repoint")
    assert repoint.ok is False


def test_as_dict_is_json_shaped():
    cap = plan_capstone(sample_inventory(), "recon", Scope())
    d = cap.as_dict()
    assert d["capture_iface"] == "wlan1"
    assert isinstance(d["steps"], list) and d["steps"][0]["step"] == "inventory"
    assert d["posture"] in ("passive", "active")
