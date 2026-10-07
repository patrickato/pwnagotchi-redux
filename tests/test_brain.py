from redux.core import Brain, SignalBus, Signal, attach_brain
from redux.core.brain import LOW_BATTERY, CRITICAL_BATTERY, PRODUCTIVE_CAPTURES
from redux.radio import Intent


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, s): self.t += s


class Ev:
    def __init__(self, type): self.type = type


def test_low_battery_forces_conserve():
    b = Brain()
    b.observe_battery(LOW_BATTERY - 1, charging=False)
    d = b.recommend(current=Intent.HUNT)
    assert d.intent is Intent.RECON and "battery low" in d.reason


def test_critical_battery_beats_productive():
    b = Brain()
    # lots of captures AND critical battery -> battery wins (safety first)
    for _ in range(5):
        b.observe(_emit(Signal.EVENT, event=Ev("handshake")))
    b.observe_battery(CRITICAL_BATTERY - 1, charging=False)
    d = b.recommend(current=Intent.HUNT)
    assert d.intent is Intent.RECON and "critical" in d.reason


def test_charging_ignores_low_battery():
    b = Brain()
    b.observe_battery(5, charging=True)      # low but charging -> not a power concern
    d = b.recommend(current=Intent.RECON)
    assert "battery" not in d.reason


def test_productive_spot_recommends_hunt():
    b = Brain()
    for _ in range(PRODUCTIVE_CAPTURES):
        b.observe(_emit(Signal.EVENT, event=Ev("handshake")))
    d = b.recommend(current=Intent.RECON)
    assert d.intent is Intent.HUNT and "keep hunting" in d.reason


def test_quiet_area_suggests_move():
    b = Brain()
    # no captures, no new APs -> tapped out
    d = b.recommend(current=Intent.HUNT)
    assert d.intent is Intent.SURVEY and ("tapped out" in d.reason or "moving" in d.reason)


def test_window_prunes_old_activity():
    clk = Clock()
    b = Brain(clock=clk, window_s=60.0)
    for _ in range(3):
        b.observe(_emit(Signal.EVENT, event=Ev("handshake")))
    clk.advance(120)                          # captures age out of the window
    d = b.recommend(current=Intent.HUNT)
    assert d.intent is not Intent.HUNT        # no longer "productive"


def test_attach_to_bus_feeds_brain():
    bus = SignalBus()
    b = Brain()
    attach_brain(bus, b)
    for _ in range(PRODUCTIVE_CAPTURES):
        bus.emit(Signal.EVENT, event=Ev("handshake"))
    assert b.recommend(current=Intent.RECON).intent is Intent.HUNT


def test_every_decision_has_a_reason():
    b = Brain()
    for cur in (Intent.ONLINE, Intent.HUNT, Intent.RECON, Intent.SURVEY):
        assert b.recommend(current=cur).reason  # glass-box: never silent


def _emit(signal, **payload):
    from redux.core import Emission
    import time
    return Emission(signal=signal.value, payload=payload, at=time.time())
