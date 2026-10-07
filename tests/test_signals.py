from redux.core import SignalBus, Signal, WILDCARD, Narrator, connect_narrator, Supervisor
from redux.radio import Radio, Intent


# --- bus basics -------------------------------------------------------------- #

def test_emit_calls_subscribers_and_records_history():
    bus = SignalBus()
    seen = []
    bus.on(Signal.REASON, lambda em: seen.append(em.payload["reason"]))
    n = bus.emit(Signal.REASON, reason="hello")
    assert n == 1 and seen == ["hello"]
    assert bus.latest(Signal.REASON).payload["reason"] == "hello"
    assert len(bus.history()) == 1


def test_wildcard_sees_everything():
    bus = SignalBus()
    allsigs = []
    bus.on(WILDCARD, lambda em: allsigs.append(em.signal))
    bus.emit(Signal.INTENT_CHANGED, intent="hunt")
    bus.emit(Signal.ALERT, alert=None)
    assert allsigs == ["intent_changed", "alert"]


def test_unsubscribe():
    bus = SignalBus()
    hits = []
    off = bus.on(Signal.REASON, lambda em: hits.append(1))
    bus.emit(Signal.REASON, reason="x")
    off()
    bus.emit(Signal.REASON, reason="y")
    assert hits == [1]


def test_bad_subscriber_is_isolated():
    bus = SignalBus()
    good = []
    bus.on(Signal.REASON, lambda em: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.on(Signal.REASON, lambda em: good.append(1))
    ran = bus.emit(Signal.REASON, reason="x")
    assert good == [1]            # the good handler still ran
    assert ran == 1               # only the good one counted
    assert bus.errors and isinstance(bus.errors[0][2], RuntimeError)


# --- supervisor emits onto the bus ------------------------------------------- #

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True, driver="mt76x2u", usb_gen=3, high_draw=True)


def test_supervisor_emits_intent_assignment_and_capture():
    bus = SignalBus()
    got = {}
    bus.on(Signal.INTENT_CHANGED, lambda em: got.__setitem__("intent", em.payload["intent"]))
    bus.on(Signal.ASSIGNMENT, lambda em: got.__setitem__("assign", em.payload["assignment"]))
    bus.on(Signal.CAPTURE_IFACE, lambda em: got.__setitem__("cap", em.payload["iface"]))
    sup = Supervisor([ONBOARD, ALFA], intent=Intent.ONLINE, bus=bus)
    sup.set_intent(Intent.HUNT)
    assert got["intent"] == "hunt"
    assert got["assign"] is not None
    assert got["cap"] == "wlan1"          # hunt -> Alfa captures


def test_supervisor_hotplug_signals():
    bus = SignalBus()
    adds, removes = [], []
    bus.on(Signal.HOTPLUG_ADD, lambda em: adds.append(em.payload["iface"]))
    bus.on(Signal.HOTPLUG_REMOVE, lambda em: removes.append(em.payload["iface"]))
    sup = Supervisor([ONBOARD], intent=Intent.HUNT, bus=bus)
    sup.add_radio(ALFA)
    sup.remove_radio("wlan1")
    assert adds == ["wlan1"] and removes == ["wlan1"]


# --- bus -> narrator wiring (the recommended glass-box path) ------------------ #

def test_connect_narrator_routes_reasons_and_alerts():
    bus = SignalBus()
    narr = Narrator()
    connect_narrator(bus, narr)
    sup = Supervisor([ONBOARD, ALFA], intent=Intent.HUNT, bus=bus)  # emits reasons on construct
    # the orchestrator's reasons flowed bus -> narrator
    assert narr.lines(), "narrator should have voiced the supervisor's reasons via the bus"

    class A:
        reason = "rogue AP on an unexpected BSSID"
        severity = "critical"
        class kind:  # noqa
            value = "rogue_ap"
    bus.emit(Signal.ALERT, alert=A())
    assert any("rogue_ap" in l.text for l in narr.lines())
