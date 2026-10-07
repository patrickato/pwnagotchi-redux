"""Pack manager — discover, resolve, enable/disable Beast Packs (task 2.4).

Pure logic over a packs directory: it finds pack manifests, resolves a safe
install/load order from their `requires` (topological, with cycle + missing-dep
detection), and tracks which packs are enabled (persisted to a small JSON state
file). It does not install apt packages or import modules — that's the image side
(Codex's lane) and the supervisor's job; this decides *what* and *in what order*,
glass-box, so a human can see the plan before anything runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .manifest import Pack, load_manifest, MANIFEST_NAMES


class DependencyError(Exception):
    """A pack requires something missing, or the requires form a cycle. Message
    explains exactly which, so it's actionable."""


class PackManager:
    def __init__(self, root, state_path=None):
        self.root = Path(root)
        self._state_path = Path(state_path) if state_path else (self.root / ".packs-state.json")
        self._packs: Dict[str, Pack] = {}
        self._enabled: set = set()
        self.discover()
        self._load_state()

    # --- discovery --------------------------------------------------------- #

    def discover(self) -> List[Pack]:
        """Scan root for subdirectories carrying a pack manifest."""
        self._packs = {}
        if not self.root.is_dir():
            return []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            for name in MANIFEST_NAMES:
                m = child / name
                if m.is_file():
                    pack = load_manifest(m)
                    self._packs[pack.name] = pack
                    break
        return list(self._packs.values())

    # --- query ------------------------------------------------------------- #

    def list(self) -> List[Pack]:
        return list(self._packs.values())

    def get(self, name: str) -> Optional[Pack]:
        return self._packs.get(name)

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    # --- dependency resolution -------------------------------------------- #

    def resolve_order(self, names=None) -> List[str]:
        """Topological order so every pack loads after its `requires`. Raises
        DependencyError on a missing dependency or a cycle, naming it."""
        targets = list(names) if names is not None else list(self._packs)
        order: List[str] = []
        seen: set = set()
        visiting: set = set()

        def visit(n, trail):
            if n in seen:
                return
            if n not in self._packs:
                raise DependencyError(
                    f"'{trail[-2]}' requires '{n}', which is not installed" if len(trail) > 1
                    else f"pack '{n}' is not installed")
            if n in visiting:
                cycle = " -> ".join(trail[trail.index(n):] + [n])
                raise DependencyError(f"dependency cycle: {cycle}")
            visiting.add(n)
            for dep in self._packs[n].requires:
                visit(dep, trail + [dep])
            visiting.discard(n)
            seen.add(n)
            order.append(n)

        for t in targets:
            visit(t, [t])
        return order

    # --- enable / disable -------------------------------------------------- #

    def enable(self, name: str) -> List[str]:
        """Enable a pack and everything it requires (in order). Returns the names
        newly enabled. Raises DependencyError if deps are unsatisfiable."""
        if name not in self._packs:
            raise DependencyError(f"pack '{name}' is not installed")
        order = self.resolve_order([name])
        newly = [n for n in order if n not in self._enabled]
        self._enabled.update(order)
        self._save_state()
        return newly

    def disable(self, name: str) -> List[str]:
        """Disable a pack and anything that (transitively) requires it. Returns the
        names newly disabled — you can't leave a dependent enabled without its dep."""
        if name not in self._enabled:
            return []
        dependents = self._dependents_of(name)
        remove = {name} | dependents
        newly = [n for n in remove if n in self._enabled]
        self._enabled -= remove
        self._save_state()
        return newly

    def enabled_packs(self) -> List[str]:
        """Enabled packs in dependency (load) order."""
        return [n for n in self.resolve_order(list(self._enabled)) if n in self._enabled]

    def _dependents_of(self, name: str) -> set:
        out, changed = set(), True
        while changed:
            changed = False
            for pack in self._packs.values():
                if pack.name in out:
                    continue
                if name in pack.requires or (out & set(pack.requires)):
                    out.add(pack.name)
                    changed = True
        return out

    # --- state persistence ------------------------------------------------- #

    def _load_state(self) -> None:
        try:
            data = json.loads(self._state_path.read_text())
            self._enabled = {n for n in data.get("enabled", []) if n in self._packs}
        except (FileNotFoundError, ValueError):
            self._enabled = set()

    def _save_state(self) -> None:
        try:
            self._state_path.write_text(json.dumps({"enabled": sorted(self._enabled)}, indent=2))
        except OSError:
            pass  # state is best-effort; never crash the manager on a read-only fs
