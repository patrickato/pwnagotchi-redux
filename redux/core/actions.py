"""Actions + transactions — the request side of the spine (completes task 2.1).

Signals are *facts* ("this happened"). Actions are *requests* ("please do this").
A consumer registers a handler for a named action; anyone can `request()` it. Every
request is announced on the SignalBus (name, args, result) so the whole system —
and a human reading the log — can see what was asked and what came back (glass-box).

A transaction groups related requests under one id with begin/end markers, so a
multi-step operation (e.g. switch intent + repoint radios) reads as one unit in the
history. The spec registry lets you introspect what actions exist and what they do.

Pure plumbing. Requests do only what their registered handler does; firing-capable
handlers remain behind their own gates (e.g. the driver's empty allowlist).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .signals import Signal


# well-known action names (handlers are registered by whoever owns the capability)
class Action:
    SET_INTENT = "set_intent"
    ADD_RADIO = "add_radio"
    REMOVE_RADIO = "remove_radio"
    PUMP = "pump"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str = ""
    args: tuple = ()


class ActionError(Exception):
    """Raised when requesting an unregistered action."""


class ActionRegistry:
    """One handler per action name (request/response), announced on a SignalBus."""

    def __init__(self, bus=None):
        self._bus = bus
        self._handlers: Dict[str, Callable[..., Any]] = {}
        self._specs: Dict[str, ActionSpec] = {}
        self._tx = itertools.count(1)
        self._current_tx: Optional[int] = None
        self._tx_actions: List[str] = []

    def register(self, name: str, handler: Callable[..., Any], spec: Optional[ActionSpec] = None):
        """Register (or replace) the handler for `name`. Returns self for chaining."""
        self._handlers[name] = handler
        self._specs[name] = spec or ActionSpec(name=name)
        return self

    def has(self, name: str) -> bool:
        return name in self._handlers

    def specs(self) -> List[ActionSpec]:
        return list(self._specs.values())

    def request(self, name: str, **args) -> Any:
        """Invoke the action's handler, announce it on the bus, return its result."""
        if name not in self._handlers:
            raise ActionError(f"no handler registered for action '{name}'")
        result = self._handlers[name](**args)
        if self._current_tx is not None:
            self._tx_actions.append(name)
        if self._bus is not None:
            self._bus.emit(Signal.ACTION, name=name, args=dict(args),
                           result=result, tx=self._current_tx)
        return result

    # --- transactions ------------------------------------------------------ #

    def transaction(self, label: str = ""):
        return _Transaction(self, label)


class _Transaction:
    def __init__(self, registry: ActionRegistry, label: str):
        self._r = registry
        self._label = label
        self.id: Optional[int] = None

    def __enter__(self):
        self.id = next(self._r._tx)
        self._r._current_tx = self.id
        self._r._tx_actions = []
        if self._r._bus is not None:
            self._r._bus.emit(Signal.TX_BEGIN, tx=self.id, label=self._label)
        return self

    def __exit__(self, exc_type, exc, tb):
        done = list(self._r._tx_actions)
        if self._r._bus is not None:
            self._r._bus.emit(Signal.TX_END, tx=self.id, label=self._label, actions=done)
        self._r._current_tx = None
        self._r._tx_actions = []
        return False  # never swallow exceptions


def register_supervisor_actions(registry: ActionRegistry, supervisor) -> ActionRegistry:
    """Expose a Supervisor's mutations as actions so the brain / UI / a script can
    request them through the registry instead of poking the object directly."""
    registry.register(Action.SET_INTENT, lambda intent: supervisor.set_intent(intent),
                      ActionSpec(Action.SET_INTENT, "change the operating intent", ("intent",)))
    registry.register(Action.ADD_RADIO, lambda radio: supervisor.add_radio(radio),
                      ActionSpec(Action.ADD_RADIO, "hotplug: a radio appeared", ("radio",)))
    registry.register(Action.REMOVE_RADIO, lambda iface: supervisor.remove_radio(iface),
                      ActionSpec(Action.REMOVE_RADIO, "hotplug: a radio was removed", ("iface",)))
    registry.register(Action.PUMP, lambda: supervisor.pump(),
                      ActionSpec(Action.PUMP, "run one event-pump cycle", ()))
    return registry
