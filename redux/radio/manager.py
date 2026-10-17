"""Radio manager — turn orchestrator decisions into interface mode changes.

Sits on top of the (pure) orchestrator: holds the current radios + intent, reacts
to hotplug add/remove, and plans the *minimal* set of interface mode changes to
realize the new role assignment. The planner (`plan_transition`) is pure and
unit-tested; `_iw_apply` is the thin live layer (iw/ip) that is a no-op off a Pi.

Scope: authorized / passive by default; nothing here deauths, injects, or targets.
It only sets interface modes (monitor/managed/down) for the chosen roles.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .orchestrator import Role, Intent, decide


class Mode(str, Enum):
    MONITOR = "monitor"
    MANAGED = "managed"
    DOWN = "down"


_ROLE_MODE = {
    Role.CAPTURE: Mode.MONITOR,
    Role.SCAN: Mode.MONITOR,
    Role.UPLINK: Mode.MANAGED,
    Role.BLUETOOTH: Mode.DOWN,
    Role.IDLE: Mode.DOWN,
}


def mode_for(role) -> Mode:
    return _ROLE_MODE[Role(role)]


@dataclass(frozen=True)
class Action:
    iface: str
    mode: Mode
    reason: str = ""


def plan_transition(old_roles: dict, new_roles: dict, gone=None) -> list:
    """Minimal mode changes to move from old_roles -> new_roles.

    old_roles / new_roles: {iface: Role}. `gone` = ifaces physically removed
    (don't emit actions for hardware that's no longer there). Only emits an
    action where an interface's required mode actually changes.
    """
    gone = set(gone or ())
    actions = []
    for iface, role in new_roles.items():
        target = mode_for(role)
        old = old_roles.get(iface)
        old_mode = mode_for(old) if old is not None else None
        if old_mode != target:
            actions.append(Action(iface, target, f"{old.value if old else 'new'} -> {Role(role).value}"))
    for iface, role in old_roles.items():
        if iface not in new_roles and iface not in gone and mode_for(role) != Mode.DOWN:
            actions.append(Action(iface, Mode.DOWN, f"{Role(role).value} -> released"))
    return actions


def _iw_apply(action: Action) -> None:
    """Apply a requested mode only if every bounded OS command succeeds.

    Never claim success when iw/ip are absent, time out, or reject a mode.
    The caller marks a partially applied transition out of sync, so later
    commands cannot silently compound the error without re-probing.
    """
    import subprocess

    def run(*args):
        try:
            completed = subprocess.run(
                list(args), check=False, capture_output=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("radio interface operation unavailable or timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError("radio interface operation rejected by kernel/driver")

    if action.mode is Mode.MONITOR:
        run("ip", "link", "set", action.iface, "down")
        run("iw", "dev", action.iface, "set", "type", "monitor")
        run("ip", "link", "set", action.iface, "up")
    elif action.mode is Mode.MANAGED:
        run("ip", "link", "set", action.iface, "down")
        run("iw", "dev", action.iface, "set", "type", "managed")
        run("ip", "link", "set", action.iface, "up")
    elif action.mode is Mode.DOWN:
        run("ip", "link", "set", action.iface, "down")


class RadioManager:
    """Stateful wrapper: current radios + intent -> applied interface modes.

    `applier` is injected (defaults to the live iw layer) so the whole thing is
    testable with a fake that just records Actions.
    """

    def __init__(self, radios=None, intent=Intent.RECON, applier=None):
        self._radios = {r.iface: r for r in (radios or [])}
        self._intent = Intent(intent)
        self._applier = applier or _iw_apply
        self._assignment = decide(self._radios.values(), self._intent)
        self._out_of_sync = False

    @property
    def assignment(self):
        return self._assignment

    @property
    def intent(self):
        return self._intent

    @property
    def out_of_sync(self) -> bool:
        """A previous mode command failed; re-probe hardware before reuse."""
        return self._out_of_sync

    def _apply_actions(self, actions: list) -> None:
        if self._out_of_sync:
            raise RuntimeError("radio state unverified after failed transition; re-probe required")
        try:
            for action in actions:
                self._applier(action)
        except Exception as exc:
            self._out_of_sync = True
            raise RuntimeError("radio transition incomplete; re-probe required") from exc

    def _recompute(self, gone=None, *, radios=None, intent=None) -> list:
        if self._out_of_sync:
            raise RuntimeError("radio state unverified after failed transition; re-probe required")
        proposed_radios = dict(self._radios) if radios is None else dict(radios)
        proposed_intent = self._intent if intent is None else Intent(intent)
        new_assignment = decide(list(proposed_radios.values()), proposed_intent)
        actions = plan_transition(self._assignment.roles, new_assignment.roles, gone=gone)
        self._apply_actions(actions)
        # Commit the in-memory plan *only after* all applier calls succeeded.
        self._radios = proposed_radios
        self._intent = proposed_intent
        self._assignment = new_assignment
        return actions

    def realize(self) -> list:
        """Apply current plan from a clean slate; stop on incomplete execution."""
        actions = plan_transition({}, self._assignment.roles)
        self._apply_actions(actions)
        return actions

    def set_intent(self, intent) -> list:
        return self._recompute(intent=Intent(intent))

    def add_radio(self, radio) -> list:
        """Hotplug: do not publish a new assignment before hardware succeeds."""
        radios = dict(self._radios)
        radios[radio.iface] = radio
        return self._recompute(radios=radios)

    def remove_radio(self, iface) -> list:
        radios = dict(self._radios)
        radios.pop(iface, None)
        return self._recompute(radios=radios, gone={iface})
