"""First-boot provisioning — the little stuff that makes the box usable on day one.

/captures is a fresh, empty partition the first time it mounts. Code paths assume
its subdirectories exist, and — the one that bites — a crack needs a WORDLIST, so
an empty wordlist store means `redux crack` has nothing to run. This creates the
full /captures skeleton and seeds a baked starter WPA wordlist (registered + set
as the default pick) so capture→crack works out of the box, before the operator
adds bigger lists (environment-grown, NAS pull, `redux wordlist add`).

Idempotent and fail-safe: run it every boot; it only creates what's missing and
never overwrites an operator's own data or re-seeds a store that already has lists.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional

# the /captures tree every subsystem expects to exist
_SKELETON = (
    "live", "incoming", "active", "ready", "crack", "audits", "wordlists",
    "loot", "loot/files", "loot/netntlm", "config", "plugins", "rules", "engagements",
)


def _starter_path() -> Path:
    """The baked starter wordlist shipped inside the package."""
    return Path(__file__).resolve().parent.parent / "data" / "starter-wordlist.txt"


def ensure_captures_skeleton(root: str = "/captures") -> List[str]:
    """Create every expected /captures subdir that's missing. Returns the ones made."""
    made: List[str] = []
    base = Path(root)
    for rel in _SKELETON:
        p = base / rel
        try:
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                made.append(str(p))
        except OSError:
            pass
    return made


def seed_starter_wordlist(root: str = "/captures") -> Optional[str]:
    """If the wordlist store is empty, copy in the baked starter and select it.
    Returns the seeded path, or None if a list already exists / seeding failed."""
    wl_dir = Path(root) / "wordlists"
    try:
        from ..crack.wordlists import WordlistStore
        store = WordlistStore(wl_dir)
        if store.list():
            return None                       # operator already has wordlists — leave it
        starter = _starter_path()
        if not starter.is_file():
            return None
        dest = wl_dir / "starter.txt"
        if not dest.exists():
            wl_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(starter, dest)
        wl, _reason = store.register(str(dest), name="starter", tier="fast", copy=False)
        if wl is not None:
            try:
                store.set_selected(wl.id)
            except Exception:
                pass
            return str(dest)
    except Exception:
        return None
    return None


def provision(root: str = "/captures") -> dict:
    """Full first-boot provisioning: skeleton + starter wordlist. Idempotent."""
    made = ensure_captures_skeleton(root)
    seeded = seed_starter_wordlist(root)
    return {"root": root, "dirs_created": made, "wordlist_seeded": seeded,
            "note": (f"created {len(made)} dir(s)" if made else "skeleton already present")
                    + (f"; seeded starter wordlist {seeded}" if seeded else "")}
