"""Hotplug entrypoint — turn a udev add/remove event into a radio re-arrangement.

Invoked by `scripts/redux-hotplug.sh` (fired from `90-redux-radio.rules`) as:

    python -m redux.radio.hotplug <add|remove> <iface>

Design note — why this re-probes instead of diffing in-process:
a udev-fired process is short-lived and holds none of the running supervisor's state, so it
cannot know the *previous* role assignment to compute a minimal diff. Instead it re-probes the
*current* set of radios (the physical truth right now), decides, and realizes the full
assignment. Setting monitor/managed/down is idempotent, so this is safe for duplicate or missed
events — the action/iface args are logging/debounce hints, not the source of truth.

When the long-running Supervisor exists (task 1.5) it owns a persistent `RadioManager` and can do
true minimal-diff transitions with richer reasons; this entrypoint becomes a notifier to it. Until
then this cold-realize path means "roles re-apply with no manual steps" already works.

Scope: only sets interface modes for role assignment. Never deauths, injects, or targets.
"""
from __future__ import annotations

import os
import sys

from .orchestrator import Intent
from .manager import RadioManager

#: passive-by-default intent when nothing else is configured (repo scope rule).
_DEFAULT_INTENT = Intent.RECON


def resolve_intent(env=None) -> Intent:
    """Current intent from env/config, defaulting to the passive RECON intent.

    Kept tiny and pure so it is unit-testable without hardware. The Supervisor will later own
    the authoritative intent; reading REDUX_INTENT lets the hotplug path honor a set intent even
    with no supervisor running.
    """
    env = os.environ if env is None else env
    raw = (env.get("REDUX_INTENT") or "").strip().lower()
    if not raw:
        return _DEFAULT_INTENT
    try:
        return Intent(raw)
    except ValueError:
        return _DEFAULT_INTENT


def run(action: str, iface: str, probe_fn=None, applier=None, env=None) -> list:
    """Re-probe, decide, and realize the full current assignment.

    `probe_fn` / `applier` are injectable for tests. Returns the applied actions (empty list if
    live probing isn't available on this build yet).
    """
    if probe_fn is None:
        try:
            from .probe import probe as probe_fn  # type: ignore
        except ImportError:
            # probe lives on the radio-probe task branch; until it lands here the live
            # enumerate step isn't wired. Fail soft and loud rather than crash a udev child.
            print(f"redux.hotplug: event {action} {iface} received, but live probe is not "
                  f"available on this build yet (task 1.2) — no mode changes applied.",
                  file=sys.stderr)
            return []

    radios = probe_fn()
    intent = resolve_intent(env)
    mgr = RadioManager(radios, intent=intent, applier=applier)
    actions = mgr.realize()
    for a in actions:
        print(f"redux.hotplug: {a.iface} -> {a.mode.value} ({a.reason})")
    if mgr.assignment.warnings:
        for w in mgr.assignment.warnings:
            print(f"redux.hotplug: warning: {w}", file=sys.stderr)
    return actions


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ("add", "remove"):
        print("usage: python -m redux.radio.hotplug <add|remove> <iface>", file=sys.stderr)
        return 2
    action, iface = argv
    run(action, iface)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
