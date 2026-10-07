from redux.compat import Plugin, PluginHost
from redux.core import SignalBus, Signal
from redux.engine import normalize_event


class SamplePlugin(Plugin):
    """A representative pwnagotchi-style plugin."""
    __author__ = "test"

    def __init__(self):
        super().__init__()
        self.events = []

    def on_loaded(self):
        self.events.append(("loaded", self.options.get("greeting", "")))

    def on_handshake(self, agent, filename, access_point, client_station):
        self.events.append(("handshake", access_point["mac"], filename))

    def on_wifi_update(self, agent, access_points):
        self.events.append(("wifi", len(access_points)))

    def on_ui_update(self, ui):
        ui.set("status", "hi")
        self.events.append(("ui",))

    def on_unload(self, ui=None):
        self.events.append(("unload",))


def test_loads_plugin_with_options_and_runs_lifecycle():
    host = PluginHost()
    p = host.load(SamplePlugin, options={"greeting": "yo"})
    assert ("loaded", "yo") in p.events
    assert host.plugins == [p]


def test_bus_handshake_reaches_on_handshake():
    bus = SignalBus()
    host = PluginHost(bus)
    p = host.load(SamplePlugin)
    bus.emit(Signal.EVENT, event=normalize_event(
        {"tag": "wifi.client.handshake", "data": {"ap": "aa:bb:cc:dd:ee:ff"}}))
    assert any(e[0] == "handshake" and e[1] == "aa:bb:cc:dd:ee:ff" for e in p.events)


def test_bus_ap_reaches_on_wifi_update():
    bus = SignalBus()
    host = PluginHost(bus)
    p = host.load(SamplePlugin)
    bus.emit(Signal.EVENT, event=normalize_event(
        {"tag": "wifi.ap.new", "data": {"mac": "aa:bb:cc:dd:ee:01", "essid": "X"}}))
    assert ("wifi", 1) in p.events


def test_assignment_triggers_ui_update():
    bus = SignalBus()
    host = PluginHost(bus)
    p = host.load(SamplePlugin)
    bus.emit(Signal.ASSIGNMENT, assignment=object())
    assert ("ui",) in p.events
    assert host.ui.get("status") == "hi"


def test_a_crashing_plugin_never_takes_down_the_host():
    class Bad(Plugin):
        def on_handshake(self, *a): raise RuntimeError("boom")
    bus = SignalBus()
    host = PluginHost(bus)
    good = host.load(SamplePlugin)
    host.load(Bad)
    bus.emit(Signal.EVENT, event=normalize_event(
        {"tag": "wifi.client.handshake", "data": {"ap": "x"}}))
    assert any(e[0] == "handshake" for e in good.events)   # good still ran
    assert host.errors and host.errors[0][1] == "on_handshake"


def test_unload_runs_on_unload():
    host = PluginHost()
    p = host.load(SamplePlugin)
    host.unload_all()
    assert ("unload",) in p.events and host.plugins == []
