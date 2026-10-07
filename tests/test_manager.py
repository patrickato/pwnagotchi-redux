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
    assert m["wlan1"] is Mode.MONITOR     # Alfa -> capture
    assert m["wlan0"] is Mode.MANAGED     # onboard -> uplink


def test_intent_switch_only_moves_what_changed():
    rec = []
    mgr = RadioManager([ONBOARD, ALFA], intent=Intent.ONLINE, applier=rec.append)
    rec.clear()
    mgr.set_intent(Intent.HUNT)
    # online: onboard=uplink(managed), alfa=idle(down). hunt: alfa=capture(monitor),
    # onboard stays uplink(managed) -> only alfa moves.
    assert _modes(rec) == {"wlan1": Mode.MONITOR}


def test_hotplug_promote_then_unplug_fallback():
    rec = []
    mgr = RadioManager([ONBOARD], intent=Intent.HUNT, applier=rec.append)

    rec.clear()
    mgr.add_radio(ALFA)                    # promote Alfa to capture, onboard -> uplink
    m = _modes(rec)
    assert m["wlan1"] is Mode.MONITOR
    assert m["wlan0"] is Mode.MANAGED

    rec.clear()
    mgr.remove_radio("wlan1")              # Alfa gone -> onboard back to capture
    m = _modes(rec)
    assert m["wlan0"] is Mode.MONITOR
    assert "wlan1" not in m                # no action for the physically-removed radio


def test_mode_for_roles():
    assert mode_for(Role.CAPTURE) is Mode.MONITOR
    assert mode_for(Role.UPLINK) is Mode.MANAGED
    assert mode_for(Role.IDLE) is Mode.DOWN
