"""Supervisor (skeleton) — Beastcore's promoted role.

Where pwnagotchi's agent/epoch loop used to sit. Owns the Radio Orchestrator,
the bettercap driver, and the (glass-box) brain seat, and is the hub for the
Beast data bus. This is a wiring skeleton; real loop lands incrementally.
"""
from __future__ import annotations

from ..radio import decide, Intent
from ..engine import BettercapDriver


class Supervisor:
    def __init__(self, radios=None, intent=Intent.RECON):
        self._radios = list(radios or [])
        self._intent = Intent(intent)
        self._engine = BettercapDriver()
        self._assignment = None

    def set_intent(self, intent) -> None:
        self._intent = Intent(intent)
        self._reassign()

    def set_radios(self, radios) -> None:
        self._radios = list(radios)
        self._reassign()

    def _reassign(self):
        self._assignment = decide(self._radios, self._intent)
        # live: for iface marked CAPTURE, bring up monitor and
        # self._engine.set_interface(iface). (TASKS.md)
        return self._assignment

    @property
    def assignment(self):
        return self._assignment
