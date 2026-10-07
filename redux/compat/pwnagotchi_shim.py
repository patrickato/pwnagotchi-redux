"""pwnagotchi-plugin compat shim (task 2.4).

Lets an existing pwnagotchi plugin run under redux without a rewrite. A pwnagotchi
plugin is a class with `self.options` and `on_*` hooks (on_loaded, on_handshake,
on_wifi_update, on_ui_update, …) that pwnagotchi calls with its agent/ui objects.
redux has no pwnagotchi agent, so this shim:

  - provides a compatible `Plugin` base (many plugins subclass one that doesn't
    exist on this fork — the loader ignoring `__defaults__` is why);
  - instantiates the plugin, injects `.options`, and runs its lifecycle;
  - routes redux SignalBus events to the hooks we can honestly supply, passing
    lightweight stand-in `agent`/`ui` objects so hook bodies don't crash.

Honest limits: hooks that need a live pwnagotchi agent/epoch (on_epoch, on_ai_*,
deep agent calls) get stand-ins and may no-op. This runs the common data/lifecycle
hooks, not pwnagotchi's full internals. Passive — it dispatches callbacks; it
drives no radio itself.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..core.signals import Signal

_log = logging.getLogger("redux.compat.pwnagotchi")


class Plugin:
    """Compatible base: default no-op hooks + an options dict, like
    pwnagotchi.plugins.Plugin. A ported plugin can subclass this."""
    __author__ = ""
    __version__ = "0.0.0"

    def __init__(self):
        self.options: dict = {}

    # lifecycle
    def on_loaded(self): pass
    def on_ready(self, agent): pass
    def on_unload(self, ui=None): pass
    # data
    def on_wifi_update(self, agent, access_points): pass
    def on_handshake(self, agent, filename, access_point, client_station): pass
    def on_ui_setup(self, ui): pass
    def on_ui_update(self, ui): pass
    def on_internet_available(self, agent): pass


class _StandInUI:
    """Minimal stand-in for pwnagotchi's UI: set/get labels, no display."""
    def __init__(self): self._state: Dict[str, object] = {}
    def set(self, key, value): self._state[key] = value
    def get(self, key, default=None): return self._state.get(key, default)
    def add_element(self, *a, **k): pass
    def remove_element(self, *a, **k): pass


class _StandInAgent:
    """Minimal stand-in for pwnagotchi's agent. Methods no-op; attributes empty."""
    def __init__(self, bus=None): self._bus = bus
    def __getattr__(self, _name):           # any unknown method -> no-op callable
        return lambda *a, **k: None


class PluginHost:
    """Loads pwnagotchi-style plugins and dispatches hooks from a SignalBus."""

    def __init__(self, bus=None):
        self._bus = bus
        self._plugins: List[Plugin] = []
        self.ui = _StandInUI()
        self.agent = _StandInAgent(bus)
        self.errors: list = []
        if bus is not None:
            self.connect(bus)

    # --- loading ----------------------------------------------------------- #

    def load(self, plugin, options: Optional[dict] = None) -> Plugin:
        """Load a plugin (a class or an instance). Injects options, runs on_loaded /
        on_ui_setup / on_ready. Returns the instance."""
        inst = plugin() if isinstance(plugin, type) else plugin
        inst.options = dict(options or getattr(inst, "options", {}) or {})
        self._safe(inst, "on_loaded")
        self._safe(inst, "on_ui_setup", self.ui)
        self._safe(inst, "on_ready", self.agent)
        self._plugins.append(inst)
        return inst

    def unload_all(self) -> None:
        for p in self._plugins:
            self._safe(p, "on_unload", self.ui)
        self._plugins = []

    @property
    def plugins(self) -> List[Plugin]:
        return list(self._plugins)

    # --- bus wiring -------------------------------------------------------- #

    def connect(self, bus) -> None:
        self._bus = bus
        bus.on(Signal.EVENT, self._on_event)
        bus.on(Signal.ASSIGNMENT, self._on_assignment)

    def _on_event(self, em) -> None:
        ev = em.payload.get("event")
        etype = getattr(ev, "type", "")
        data = getattr(ev, "data", {}) or {}
        if etype == "handshake":
            ap = data.get("ap") or data.get("station") or ""
            fname = data.get("file", "") or f"{ap}.pcap"
            self.dispatch("on_handshake", self.agent, fname, {"mac": ap}, {})
        elif etype in ("ap.new", "ap.lost"):
            self.dispatch("on_wifi_update", self.agent, [data])

    def _on_assignment(self, em) -> None:
        self.dispatch("on_ui_update", self.ui)

    # --- dispatch ---------------------------------------------------------- #

    def dispatch(self, hook: str, *args) -> int:
        """Call `hook` on every loaded plugin, isolating exceptions. Returns how
        many ran without error."""
        ran = 0
        for p in self._plugins:
            if self._safe(p, hook, *args):
                ran += 1
        return ran

    def _safe(self, plugin, hook, *args) -> bool:
        fn = getattr(plugin, hook, None)
        if not callable(fn):
            return False
        try:
            fn(*args)
            return True
        except Exception as exc:  # a plugin must never crash the host
            self.errors.append((getattr(plugin, "__class__", type(plugin)).__name__, hook, exc))
            _log.warning("plugin %s hook %s raised: %s", type(plugin).__name__, hook, exc)
            return False
