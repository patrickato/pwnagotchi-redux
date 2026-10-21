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


def test_plan_exposes_unexecuted_unknown_even_when_radio_engine_ready():
    cap = plan_capstone(sample_inventory(), "recon", Scope(), engine_available=True)
    report = cap.as_dict()
    assert cap.ok is True
    assert report["plan_valid"] is True
    assert report["execution_performed"] is False
    assert report["physical_acceptance"] == "UNKNOWN"
    assert "NOT been started or verified" in cap.summary


def test_live_cli_only_reads_inventory_never_runs_capture(monkeypatch, capsys):
    """A live probe is NEVER evidence of role application / a frame capture."""
    import json
    from types import SimpleNamespace
    from redux import cli
    from redux.radio import probe as probe_module

    events = []
    def sample_probe():
        events.append("probe")
        return sample_inventory()

    # No real hardware access in this test; all live inventory is synthetic.
    import redux.radio.probe as probe_file
    monkeypatch.setattr(probe_file, "probe", sample_probe)
    monkeypatch.setattr(cli.Scope, "load", lambda path: Scope())
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    args = SimpleNamespace(
        live=True, intent="recon", seconds=30.0, scope_file="synthetic",
        persona=None, offense=False, no_engine=False, json=True)
    assert cli.cmd_demo(args) == 3
    report = json.loads(capsys.readouterr().out)
    assert events == ["probe"]
    assert report["mode"] == "read_only_hardware_probe_plan"
    assert report["plan_valid"] is True
    assert report["execution_performed"] is False
    assert report["physical_acceptance"] == "UNKNOWN"


def test_synthetic_cli_demo_never_looks_like_accepted_hardware(capsys, monkeypatch):
    import json
    from types import SimpleNamespace
    from redux import cli
    monkeypatch.setattr(cli.Scope, "load", lambda path: Scope())
    args = SimpleNamespace(
        live=False, intent="recon", seconds=30.0, scope_file="synthetic",
        persona=None, offense=False, no_engine=False, json=True)
    assert cli.cmd_demo(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "synthetic_dry_run"
    assert report["execution_performed"] is False
    assert report["physical_acceptance"] == "UNKNOWN"
