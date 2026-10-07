from redux.radio import Radio, Intent, Mode
from redux.radio import hotplug

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
             driver="mt76x2u", usb_gen=3, high_draw=True)


def test_resolve_intent_defaults_to_passive():
    assert hotplug.resolve_intent(env={}) is Intent.RECON
    assert hotplug.resolve_intent(env={"REDUX_INTENT": ""}) is Intent.RECON
    assert hotplug.resolve_intent(env={"REDUX_INTENT": "nonsense"}) is Intent.RECON


def test_resolve_intent_honors_env():
    assert hotplug.resolve_intent(env={"REDUX_INTENT": "hunt"}) is Intent.HUNT
    assert hotplug.resolve_intent(env={"REDUX_INTENT": "ONLINE"}) is Intent.ONLINE


def test_run_realizes_full_assignment_from_injected_probe():
    rec = []
    actions = hotplug.run("add", "wlan1",
                          probe_fn=lambda: [ONBOARD, ALFA],
                          applier=rec.append,
                          env={"REDUX_INTENT": "hunt"})
    modes = {a.iface: a.mode for a in actions}
    # hunt: Alfa (injector, 5GHz) -> capture/monitor; onboard -> uplink/managed
    assert modes["wlan1"] is Mode.MONITOR
    assert modes["wlan0"] is Mode.MANAGED
    assert [a.iface for a in rec] == [a.iface for a in actions]


def test_run_degrades_cleanly_without_probe():
    # On this branch probe.py isn't present; run() must fail soft, not raise.
    assert hotplug.run("add", "wlan1") == []


def test_main_rejects_bad_args():
    assert hotplug.main(["bogus"]) == 2
    assert hotplug.main(["add"]) == 2
    assert hotplug.main(["add", "wlan1", "extra"]) == 2
