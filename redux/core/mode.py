"""Operating mode: LAB (unlocked, automatic) vs FIELD (passive, gated).

One switch, two postures:

  LAB   — scope.lab_mode ON  -> approve-on-sight, active deauth-capture, auto-crack
           (the whole chain runs itself; for your own gear).
  FIELD — scope.lab_mode OFF -> passive, no transmit, authorize-required, no
           auto-crack. The shipped default, and the lock-down for anywhere public.

Switching persists lab_mode to the scope file (keeping any armed targets) and
restarts the live daemon so the new posture takes effect from a clean re-arm.
Everything else — auto-offense (augur) and auto-crack (audit) — already keys off
lab_mode, so flipping this one flag flips the whole platform.
"""
from __future__ import annotations

import subprocess

from .scope import Scope, default_scope_path

LAB = "lab"
FIELD = "field"
MODES = (LAB, FIELD)


def read_mode(scope_path: str | None = None) -> str:
    """Current mode, read from the persisted scope. Fails safe to FIELD."""
    path = scope_path or default_scope_path()
    try:
        return LAB if Scope.load(path).lab_mode else FIELD
    except Exception:
        return FIELD


def _restart_daemon(runner=subprocess.run) -> bool:
    try:
        result = runner(["systemctl", "restart", "redux-live.service"],
                        capture_output=True, timeout=30)
        return getattr(result, "returncode", 1) == 0
    except Exception:
        return False


def set_mode(mode: str, scope_path: str | None = None, *, restart=_restart_daemon) -> dict:
    """Set LAB or FIELD: persist lab_mode (armed targets kept) and restart the
    daemon. Returns a glass-box result dict."""
    if mode not in MODES:
        return {"ok": False, "mode": read_mode(scope_path),
                "reason": f"unknown mode {mode!r} (choose lab|field)"}
    path = scope_path or default_scope_path()
    try:
        scope = Scope.load(path)
    except Exception:
        scope = Scope()
    scope.lab_mode = (mode == LAB)
    scope.save(path)
    restarted = bool(restart())
    tail = "" if restarted else " — restart redux-live.service to apply"
    return {"ok": True, "mode": mode, "restarted": restarted,
            "reason": f"mode = {mode.upper()}{tail}"}


def toggle_mode(scope_path: str | None = None, *, restart=_restart_daemon) -> dict:
    """Flip LAB <-> FIELD (what the TFT switch calls)."""
    path = scope_path or default_scope_path()
    new = FIELD if read_mode(path) == LAB else LAB
    return set_mode(new, path, restart=restart)
