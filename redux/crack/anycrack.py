"""Run a :class:`~redux.crack.recipes.CrackPlan` — the gated executor for the
guided any-hash cracker.

The Wi-Fi audit worker (`audit.py`) is scope-gated per-BSSID because it cracks
targets the device pulled off the air. This tool is different: it cracks a hash
*file the operator hands it* — an NTLM dump, a kerberoast ticket, a shadow
line. Possession of the file is the authorization, so there's no airborne
target to scope-check. To stay true to the project's "gated by default, turnkey
in LAB" contract we still require one explicit gesture before firing:

* **FIELD** (the shipped default): the operator must pass ``authorized=True``
  (the CLI's ``--authorized`` flag) — a deliberate "yes, this is mine to
  crack".
* **LAB**: unlocked. ``lab_mode=True`` runs immediately, no flag needed.

Everything is glass-box and fail-closed: a plan with problems never runs, the
runner is injected (so tests exercise the whole flow with a fake), results are
written private (0600) with a restrictive umask, and the return is a small dict
with a human reason.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .recipes import CrackPlan


@dataclass(frozen=True)
class CrackSettings:
    binary: str = "hashcat"
    results_dir: Path = Path("/captures/crack")
    max_runtime_seconds: int = 1800


def _default_runner(argv, **kw):
    return subprocess.run(argv, **kw)


def authorize(*, authorized: bool, lab_mode: bool) -> tuple[bool, str]:
    """The one gate. LAB unlocks; FIELD requires an explicit --authorized."""
    if lab_mode:
        return True, "LAB mode — any-hash cracking is unlocked"
    if authorized:
        return True, "authorized by the operator (--authorized)"
    return False, ("refused: this is gated in FIELD mode — pass --authorized to confirm "
                   "the hash file is yours to crack, or switch to LAB mode")


def run_plan(cplan: CrackPlan, settings: CrackSettings, *,
             authorized: bool = False, lab_mode: bool = False,
             runner: Optional[Callable] = None, session: str = "redux-crack") -> dict:
    """Execute a CrackPlan if (and only if) the gate permits. Returns a dict:
    ``{"status", "reason", "mode", "attack"}`` and, on a hit, ``"result"``.
    """
    run = runner or _default_runner
    base = {"mode": cplan.recipe.mode, "attack": cplan.attack,
            "recipe": cplan.recipe.name}

    if not cplan.ok:
        return {**base, "status": "refused", "reason": cplan.explanation}

    ok, why = authorize(authorized=authorized, lab_mode=lab_mode)
    if not ok:
        return {**base, "status": "refused", "reason": why}

    rdir = Path(settings.results_dir)
    rdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(rdir, 0o700)
    except OSError:
        pass
    result_path = rdir / (session + ".found")
    if result_path.is_symlink():
        return {**base, "status": "error", "reason": "result path is a symlink"}

    # Point the plan's outfile at our private result file + a stable session.
    argv = list(cplan.argv)
    if "--outfile" not in argv:
        argv += ["--outfile", str(result_path), "--outfile-format", "2"]
    argv += ["--session", session]

    old_mask = os.umask(0o077)
    try:
        proc = run(argv, capture_output=True,
                   timeout=settings.max_runtime_seconds + 30, check=False)
    except subprocess.TimeoutExpired:
        return {**base, "status": "error", "reason": "hashcat runtime exceeded"}
    except (OSError, subprocess.SubprocessError) as e:
        return {**base, "status": "error", "reason": f"hashcat failed: {type(e).__name__}"}
    finally:
        os.umask(old_mask)

    if result_path.exists():
        try:
            os.chmod(result_path, 0o600)
        except OSError:
            pass
    rc = getattr(proc, "returncode", None)
    if result_path.is_file() and result_path.stat().st_size:
        return {**base, "status": "recovered", "reason": why + "; recovery stored privately",
                "result": str(result_path)}
    if rc == 1:
        return {**base, "status": "exhausted", "reason": "wordlist/keyspace exhausted, no match"}
    if rc == 0:
        return {**base, "status": "completed", "reason": "completed without a match"}
    return {**base, "status": "error", "reason": f"hashcat exited {rc}"}
