"""Radio-manager errors must never be treated as successful mode transitions."""
import subprocess
from types import SimpleNamespace

import pytest

from redux.radio.orchestrator import Radio, Role, Intent
from redux.radio.manager import RadioManager, Action, Mode, _iw_apply


ONBOARD = Radio("wlan0", phy="phy0", monitor=True, onboard=True)
USB = Radio("wlan1", phy="phy1", monitor=True, inject=True, usb_gen=3)


def test_failed_intent_switch_preserves_previous_claims_and_blocks_retry():
    actions = []
    def fail_second(action):
        actions.append(action)
        raise OSError("simulated radio driver failure")

    mgr = RadioManager([ONBOARD, USB], intent=Intent.ONLINE, applier=fail_second)
    baseline = mgr.assignment
    with pytest.raises(RuntimeError, match="incomplete"):
        mgr.set_intent(Intent.HUNT)
    assert mgr.out_of_sync is True
    assert mgr.assignment is baseline
    assert mgr.intent is Intent.ONLINE
    before = len(actions)
    with pytest.raises(RuntimeError, match="re-probe"):
        mgr.set_intent(Intent.HUNT)
    assert len(actions) == before


def test_failed_usb_hotplug_does_not_publish_new_capture_assignment():
    calls = []
    def fail(action):
        calls.append(action)
        raise RuntimeError("no ownership proof")

    mgr = RadioManager([ONBOARD], intent=Intent.RECON, applier=fail)
    with pytest.raises(RuntimeError, match="re-probe"):
        mgr.add_radio(USB)
    assert "wlan1" not in mgr.assignment.roles
    assert mgr.out_of_sync is True
    assert len(calls) == 1


def test_failure_after_partial_hotplug_does_not_claim_success():
    actions = []
    def second_fails(action):
        actions.append(action)
        if len(actions) == 2:
            raise RuntimeError("simulated second transition failure")

    # Keeping the onboard uplink forces two changes when USB arrives:
    # onboard capture -> managed uplink, plus USB -> capture. Without it,
    # capability-scaling keeps the onboard capture as a tandem radio and
    # needs only one new interface action.
    mgr = RadioManager([ONBOARD], intent=Intent.HUNT, applier=second_fails,
                       keep_uplink=True)
    original = mgr.assignment
    with pytest.raises(RuntimeError, match="incomplete"):
        mgr.add_radio(USB)
    assert len(actions) == 2
    assert mgr.assignment is original
    assert mgr.out_of_sync is True


def test_failed_remove_keeps_prior_assignment_dirty():
    def fail(_):
        raise RuntimeError("synthetic down failure")
    # In max-capture tandem mode the remaining onboard capture needs no
    # retune when USB disappears. Keep-uplink mode does require reassignment.
    mgr = RadioManager([ONBOARD, USB], intent=Intent.HUNT, applier=fail,
                       keep_uplink=True)
    before = mgr.assignment
    with pytest.raises(RuntimeError):
        mgr.remove_radio("wlan1")
    assert mgr.assignment is before
    assert mgr.out_of_sync


def test_realize_does_not_silently_continue_on_failure():
    commands = []
    def fail(action):
        commands.append(action)
        raise RuntimeError("first command failed")
    mgr = RadioManager([ONBOARD, USB], intent=Intent.RECON, applier=fail)
    with pytest.raises(RuntimeError, match="incomplete"):
        mgr.realize()
    assert len(commands) == 1
    assert mgr.out_of_sync
    with pytest.raises(RuntimeError, match="re-probe"):
        mgr.realize()


@pytest.mark.parametrize(("behavior", "expected_calls"), [
    ("missing", 1), ("failed", 1), ("timeout", 1),
    ("type_rejected", 2),
])
def test_direct_mode_applier_stops_on_first_os_failure(monkeypatch, behavior, expected_calls):
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 8
        if behavior == "missing":
            raise FileNotFoundError("iw/ip missing")
        if behavior == "timeout":
            raise subprocess.TimeoutExpired(args, 8)
        if behavior == "failed" or behavior == "type_rejected" and len(calls) == 2:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        _iw_apply(Action("wlan1", Mode.MONITOR))
    assert len(calls) == expected_calls
    assert not any(cmd == ["ip", "link", "set", "wlan1", "up"] for cmd in calls)


def test_direct_mode_applier_full_success_requires_all_commands(monkeypatch):
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    _iw_apply(Action("wlan1", Mode.MONITOR))
    assert calls == [
        ["ip", "link", "set", "wlan1", "down"],
        ["iw", "dev", "wlan1", "set", "type", "monitor"],
        ["ip", "link", "set", "wlan1", "up"],
    ]
