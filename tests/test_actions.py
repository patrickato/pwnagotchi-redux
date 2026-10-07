import pytest

from redux.core import (SignalBus, Signal, ActionRegistry, Action, ActionError,
                        Supervisor, register_supervisor_actions)
from redux.radio import Radio, Intent


ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True, driver="mt76x2u", usb_gen=3, high_draw=True)


def test_request_calls_handler_and_returns_result():
    reg = ActionRegistry()
    reg.register("double", lambda n: n * 2)
    assert reg.request("double", n=21) == 42


def test_unregistered_action_raises():
    with pytest.raises(ActionError):
        ActionRegistry().request("nope")


def test_request_is_announced_on_the_bus():
    bus = SignalBus()
    reg = ActionRegistry(bus)
    reg.register("ping", lambda: "pong")
    reg.request("ping")
    em = bus.latest(Signal.ACTION)
    assert em.payload["name"] == "ping" and em.payload["result"] == "pong"


def test_transaction_brackets_actions():
    bus = SignalBus()
    reg = ActionRegistry(bus)
    reg.register("a", lambda: 1).register("b", lambda: 2)
    with reg.transaction("switch") as tx:
        reg.request("a")
        reg.request("b")
    begin = bus.latest(Signal.TX_BEGIN)
    end = bus.latest(Signal.TX_END)
    assert begin.payload["tx"] == tx.id
    assert end.payload["actions"] == ["a", "b"] and end.payload["label"] == "switch"


def test_supervisor_actions_drive_the_supervisor():
    bus = SignalBus()
    sup = Supervisor([ONBOARD], intent=Intent.ONLINE, bus=bus)
    reg = ActionRegistry(bus)
    register_supervisor_actions(reg, sup)
    reg.request(Action.SET_INTENT, intent=Intent.HUNT)
    assert sup.intent is Intent.HUNT
    reg.request(Action.ADD_RADIO, radio=ALFA)
    assert sup.capture_iface == "wlan1"
    reg.request(Action.REMOVE_RADIO, iface="wlan1")
    assert sup.capture_iface == "wlan0"


def test_specs_introspection():
    reg = ActionRegistry()
    sup = Supervisor([ONBOARD], intent=Intent.RECON)
    register_supervisor_actions(reg, sup)
    names = {s.name for s in reg.specs()}
    assert {Action.SET_INTENT, Action.ADD_RADIO, Action.REMOVE_RADIO, Action.PUMP} <= names
