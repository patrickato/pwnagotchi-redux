"""Signal spine — the Augur event bus (task 2.1, native).

Everything in redux converges here: the Supervisor emits what it decided and saw,
and consumers (the creature Narrator, the detectors, a web UI, a future brain,
Augur itself) subscribe. One hub, glass-box by construction — every emission
is recorded with its payload and time, so you can always see what flowed and why.

Built fresh rather than ported: synchronous, dependency-free, exception-isolated
(one bad subscriber never breaks the bus or the other subscribers). Pure plumbing
— it moves signals, it decides nothing and transmits nothing on the radio.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional


class Signal(str, Enum):
    INTENT_CHANGED = "intent_changed"    # payload: intent
    RADIOS_CHANGED = "radios_changed"    # payload: ifaces
    HOTPLUG_ADD = "hotplug_add"          # payload: iface
    HOTPLUG_REMOVE = "hotplug_remove"    # payload: iface
    ASSIGNMENT = "assignment"            # payload: assignment
    CAPTURE_IFACE = "capture_iface"      # payload: iface (or None)
    REASON = "reason"                    # payload: reason (str)
    WARNING = "warning"                  # payload: reason (str)
    ALERT = "alert"                      # payload: alert (detector Alert)
    EVENT = "event"                      # payload: event (driver Event)
    BATTERY = "battery"                  # payload: percent (0..100), charging (bool)
    SUGGESTION = "suggestion"            # payload: decision (brain Decision)
    ACTION = "action"                    # payload: name, args, result, tx
    TX_BEGIN = "tx_begin"                # payload: tx, label
    TX_END = "tx_end"                    # payload: tx, label, actions


#: subscribe to this to receive every signal
WILDCARD = "*"


@dataclass(frozen=True)
class Emission:
    signal: str
    payload: dict
    at: float


Handler = Callable[[Emission], None]


@dataclass
class SignalBus:
    """Synchronous pub/sub hub. `on()` subscribes, `emit()` publishes.

    Handlers receive one `Emission` (signal + payload + time). Exceptions in a
    handler are isolated (recorded, not raised) so a flaky subscriber can't take
    down the bus. Every emission is kept in a bounded history for glass-box
    inspection.
    """
    keep: int = 200
    clock: Callable[[], float] = time.time
    _subs: Dict[str, List[Handler]] = field(default_factory=lambda: defaultdict(list))
    _history: Deque[Emission] = field(init=False)
    errors: List[tuple] = field(default_factory=list)  # (signal, handler, exc)

    def __post_init__(self):
        self._history = deque(maxlen=self.keep)

    def on(self, signal, handler: Handler) -> Callable[[], None]:
        """Subscribe `handler` to `signal` (or WILDCARD for all). Returns an
        unsubscribe callable."""
        key = _key(signal)
        self._subs[key].append(handler)

        def _off():
            try:
                self._subs[key].remove(handler)
            except ValueError:
                pass
        return _off

    def off(self, signal, handler: Handler) -> None:
        try:
            self._subs[_key(signal)].remove(handler)
        except ValueError:
            pass

    def emit(self, signal, **payload) -> int:
        """Publish `signal` with `payload`. Returns how many handlers ran."""
        key = _key(signal)
        em = Emission(signal=key, payload=dict(payload), at=self.clock())
        self._history.append(em)
        ran = 0
        for handler in list(self._subs.get(key, ())) + list(self._subs.get(WILDCARD, ())):
            try:
                handler(em)
                ran += 1
            except Exception as exc:  # isolate: a bad subscriber never breaks the bus
                self.errors.append((key, handler, exc))
        return ran

    def history(self, signal=None) -> List[Emission]:
        if signal is None:
            return list(self._history)
        key = _key(signal)
        return [e for e in self._history if e.signal == key]

    def latest(self, signal=None) -> Optional[Emission]:
        h = self.history(signal)
        return h[-1] if h else None


def _key(signal) -> str:
    return signal.value if isinstance(signal, Signal) else str(signal)


# --------------------------------------------------------------------------- #
# Wiring helpers — connect existing consumers to the bus
# --------------------------------------------------------------------------- #

def connect_narrator(bus: SignalBus, narrator) -> None:
    """Route the glass-box bus into the creature Narrator: reasons/warnings become
    spoken decision lines, alerts become spoken alerts."""
    def _reason(em: Emission):
        narrator.say_reason(em.payload.get("reason", ""))

    def _warning(em: Emission):
        narrator.say_reason("warning: " + em.payload.get("reason", ""))

    def _alert(em: Emission):
        a = em.payload.get("alert")
        if a is not None:
            narrator.say_alert(a)

    bus.on(Signal.REASON, _reason)
    bus.on(Signal.WARNING, _warning)
    bus.on(Signal.ALERT, _alert)
