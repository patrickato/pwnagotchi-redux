"""Supervisor — Beastcore's promoted role (task 1.5, logic layer).

Where pwnagotchi's agent/epoch loop used to sit. The Supervisor owns the control
flow: an **intent** comes in, the Radio Orchestrator decides radio→role
assignments, the radio layer applies the interface modes, and the bettercap
driver is repointed at the capture radio. Every move carries a human-readable
reason (glass-box).

It is wired through small injected interfaces (`RadioControl`, `Driver`) rather
than hard dependencies, so:
  - it is unit-testable with fakes, no hardware and no bettercap, right now;
  - it drops onto the real `RadioManager` (task 1.3, #2) and `BettercapDriver`
    (task 1.4, #5) at merge with no change here.

The live creature screen (TFT) and the real event loop timing are the labeled
on-hardware gate (task 1.5 remainder); this module is the decision/wiring core.

Scope: recon/capture is passive. The Supervisor never calls a firing action; any
deauth stays behind the driver's empty-by-default allowlist gate.
"""
from __future__ import annotations

from typing import Callable, Optional, Protocol

from ..radio import decide, Intent, Role
from .event_bridge import events_to_frames
from .signals import Signal


class RadioControl(Protocol):
    """Applies a role assignment to real interface modes. Satisfied in production
    by a thin adapter over `RadioManager`; by a fake in tests."""
    def apply(self, assignment) -> list: ...


class Driver(Protocol):
    """The bettercap-facing surface the Supervisor needs. Satisfied by
    `BettercapDriver`; by a fake in tests."""
    def set_interface(self, iface: str): ...
    def recon(self, on: bool = True): ...
    def poll_events(self, clear: bool = True) -> list: ...


def capture_iface(assignment) -> Optional[str]:
    """The interface assigned the CAPTURE role, if any."""
    if assignment is None:
        return None
    for iface, role in assignment.roles.items():
        if role == Role.CAPTURE:
            return iface
    return None


class Supervisor:
    """Owns radios + intent; turns intent changes and hotplug events into applied
    radio modes and a repointed capture engine, narrating why.

    All collaborators are optional/injected: with neither `radio_control` nor
    `driver` it still computes and explains assignments (pure decision mode).
    """

    def __init__(
        self,
        radios=None,
        intent=Intent.RECON,
        decider: Callable = decide,
        radio_control: Optional[RadioControl] = None,
        driver: Optional[Driver] = None,
        log: Optional[Callable[[str], None]] = None,
        narrator=None,
        detect_engine=None,
        bus=None,
    ):
        self._radios = {r.iface: r for r in (radios or [])}
        self._intent = Intent(intent)
        self._decide = decider
        self._radio_control = radio_control
        self._driver = driver
        self._log = log or (lambda msg: None)
        self._narrator = narrator
        self._detect_engine = detect_engine
        self._bus = bus  # SignalBus hub; optional. Use a bus OR a direct narrator, not both.
        self._assignment = None
        self._capture_iface = None
        self.reasons: list = []
        self._reassign()

    # --- state ------------------------------------------------------------- #

    @property
    def assignment(self):
        return self._assignment

    @property
    def intent(self) -> Intent:
        return self._intent

    @property
    def capture_iface(self) -> Optional[str]:
        return self._capture_iface

    # --- inputs ------------------------------------------------------------ #

    def set_intent(self, intent) -> None:
        self._intent = Intent(intent)
        self._emit(Signal.INTENT_CHANGED, intent=self._intent.value)
        self._say(f"intent -> {self._intent.value}")
        self._reassign()

    @property
    def radios(self) -> list:
        """The radios currently managed (for the Doctor / capability graph)."""
        return list(self._radios.values())

    @property
    def driver(self):
        """The bettercap driver, if one is wired (for capture-engine selection)."""
        return self._driver

    def set_radios(self, radios) -> None:
        self._radios = {r.iface: r for r in radios}
        self._emit(Signal.RADIOS_CHANGED, ifaces=list(self._radios))
        self._reassign()

    def add_radio(self, radio) -> None:
        """Hotplug: a radio appeared."""
        self._radios[radio.iface] = radio
        self._emit(Signal.HOTPLUG_ADD, iface=radio.iface)
        self._say(f"hotplug + {radio.iface}")
        self._reassign()

    def remove_radio(self, iface) -> None:
        """Hotplug: a radio was removed."""
        self._radios.pop(iface, None)
        self._emit(Signal.HOTPLUG_REMOVE, iface=iface)
        self._say(f"hotplug - {iface}")
        self._reassign()

    # --- core loop step ---------------------------------------------------- #

    def _reassign(self):
        self._assignment = self._decide(list(self._radios.values()), self._intent)
        # narrate the orchestrator's own reasons (glass-box)
        for r in getattr(self._assignment, "reasons", []) or []:
            self._say(r)
        for w in getattr(self._assignment, "warnings", []) or []:
            self._say(f"warning: {w}")
        self._emit(Signal.ASSIGNMENT, assignment=self._assignment)
        # apply interface modes via the radio layer, if wired
        if self._radio_control is not None:
            self._radio_control.apply(self._assignment)
        # repoint the capture engine at the chosen capture radio, if wired
        self._repoint_driver()
        return self._assignment

    def _repoint_driver(self) -> None:
        iface = capture_iface(self._assignment)
        self._capture_iface = iface
        self._emit(Signal.CAPTURE_IFACE, iface=iface)
        if self._driver is None:
            return
        if iface is None:
            self._say("no capture radio this intent; leaving bettercap idle")
            self._driver.recon(False)
            return
        self._say(f"pointing bettercap at {iface} and starting recon")
        self._driver.set_interface(iface)
        self._driver.recon(True)

    def tick(self) -> list:
        """One event-pump step: drain driver events (normalized). Empty without a
        driver. The real timed loop + creature screen are the on-hardware gate."""
        if self._driver is None:
            return []
        return self._driver.poll_events(clear=True)

    def pump(self) -> list:
        """Full event step: drain driver events, bridge them to detector frames,
        run the detectors, and narrate any alerts. Returns the alerts (empty
        without a driver or detect engine). Passive — raises alerts only, never TX.

        Honest coverage: bettercap's REST events only bridge to beacon-class frames
        (see event_bridge), so this lights up the AP-watching detectors; raw-frame
        detectors (deauth flood, sweep) stay dark until a raw-capture tap exists."""
        events = self.tick()
        for e in events:
            self._emit(Signal.EVENT, event=e)
        if self._detect_engine is None:
            return []
        alerts = self._detect_engine.feed_many(events_to_frames(events))
        for a in alerts:
            self._emit(Signal.ALERT, alert=a)
            if self._narrator is not None:
                self._narrator.say_alert(a)
        return alerts

    def creature_lines(self, n: Optional[int] = None):
        """Recent glass-box creature narration (empty without a narrator)."""
        return self._narrator.lines(n) if self._narrator is not None else []

    def creature_tft(self) -> str:
        """One-line TFT string from the narrator (placeholder without one)."""
        return self._narrator.tft() if self._narrator is not None else "…"

    # --- glass-box --------------------------------------------------------- #

    def _say(self, reason: str) -> None:
        self.reasons.append(reason)
        self._log(reason)
        if self._narrator is not None:
            self._narrator.say_reason(reason)
        if self._bus is not None:
            if reason.lower().startswith("warning:"):
                self._emit(Signal.WARNING, reason=reason[len("warning:"):].strip())
            else:
                self._emit(Signal.REASON, reason=reason)

    def _emit(self, signal, **payload) -> None:
        if self._bus is not None:
            self._bus.emit(signal, **payload)
