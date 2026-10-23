from redux.radio import Radio, Intent
from redux.radio.manager import RadioManager, Mode, plan_transition, mode_for
from redux.radio.orchestrator import Role

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
             driver="mt76x2u", usb_gen=3, high_draw=True)


def _modes(actions):
    return {a.iface: a.mode for a in actions}


def test_plan_transition_minimal_diff():
    old = {"wlan0": Role.UPLINK, "wlan1": Role.IDLE}
    new = {"wlan0": Role.UPLINK, "wlan1": Role.CAPTURE}
    actions = plan_transition(old, new)
    # only wlan1 changed (idle/down -> capture/monitor); wlan0 unchanged
    assert _modes(actions) == {"wlan1": Mode.MONITOR}


def test_realize_sets_both_from_cold():
    mgr = RadioManager([ONBOARD, ALFA], intent=Intent.HUNT, applier=lambda a: None)
    actions = mgr.realize()
    m = _modes(actions)
    assert m["wlan1"] is Mode.MONITOR     # Alfa -> primary active capture
    assert m["wlan0"] is Mode.MONITOR     # onboard -> tandem passive capture (both hunt)


def test_realize_keep_uplink_holds_onboard_managed():
    mgr = RadioManager([ONBOARD, ALFA], intent=Intent.HUNT,
                       applier=lambda a: None, keep_uplink=True)
    m = _modes(mgr.realize())
    assert m["wlan1"] is Mode.MONITOR     # Alfa captures
    assert m["wlan0"] is Mode.MANAGED     # opt-in: onboard held online


def test_intent_switch_only_moves_what_changed():
    rec = []
    mgr = RadioManager([ONBOARD, ALFA], intent=Intent.ONLINE, applier=rec.append)
    rec.clear()
    mgr.set_intent(Intent.HUNT)
    # online: onboard=uplink(managed), alfa=idle(down). hunt: alfa=capture(monitor),
    # onboard joins as tandem(monitor) -> both move.
    assert _modes(rec) == {"wlan1": Mode.MONITOR, "wlan0": Mode.MONITOR}


def test_hotplug_promote_then_unplug_keeps_onboard_capturing():
    rec = []
    mgr = RadioManager([ONBOARD], intent=Intent.HUNT, applier=rec.append)
    assert mgr.assignment.roles["wlan0"] is Role.CAPTURE   # onboard-only: it captures

    rec.clear()
    mgr.add_radio(ALFA)                    # Alfa takes active capture; onboard -> tandem
    m = _modes(rec)
    assert m["wlan1"] is Mode.MONITOR
    assert "wlan0" not in m                # already monitor as CAPTURE -> no thrash
    assert mgr.assignment.roles["wlan1"] is Role.CAPTURE
    assert mgr.assignment.roles["wlan0"] is Role.SCAN

    rec.clear()
    mgr.remove_radio("wlan1")              # Alfa gone -> onboard resumes active capture
    assert mgr.assignment.roles["wlan0"] is Role.CAPTURE   # never goes dark
    assert "wlan1" not in _modes(rec)      # no action for the physically-removed radio


def test_mode_for_roles():
    assert mode_for(Role.CAPTURE) is Mode.MONITOR
    assert mode_for(Role.UPLINK) is Mode.MANAGED
    assert mode_for(Role.IDLE) is Mode.DOWN
