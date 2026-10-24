"""Wordlist registry for the on-device WPA audit (hashcat).

The audit worker (`redux/crack/audit.py`) takes a single wordlist path. This is
the store that manages MANY of them: register local lists, pull one from a NAS
(e.g. a QNAP) over an *injected* fetcher, group them into tiers
(fast / standard / thorough), and select one by name, id, or tier with a
glass-box reason.

Real data only: a list is registered only once its bytes exist on disk and its
sha256 is recorded, so a selection can never resolve to a path that isn't really
a wordlist. Dedup is by content hash, so the same list registered twice (or
pulled again from the NAS) collapses to one entry.

Pure / testable: the NAS pull takes an injected ``fetcher(spec, dest) -> path``,
so the whole flow is exercised with a local fake and no network. The index is a
plain JSON file in the store directory, written atomically.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .ingest import _digest_file

# Smallest/fastest first. A tier is a human label for "how hard do I want to try",
# not a guarantee about size — the operator assigns it when registering a list.
TIERS: Tuple[str, ...] = ("fast", "standard", "thorough")

_INDEX_NAME = "index.json"
_SELECTED_NAME = ".selected"      # sidecar holding the operator's persisted pick (a wordlist id)


@dataclass(frozen=True)
class Wordlist:
    id: str                 # first 12 hex of the content sha256 — stable across re-adds
    name: str               # operator-facing label (defaults to the file basename)
    path: str               # absolute path to the bytes on this device
    tier: str               # one of TIERS
    size_bytes: int
    sha256: str
    source: str             # "local" or "nas:<spec>"
    added: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Wordlist":
        return cls(id=d["id"], name=d["name"], path=d["path"], tier=d["tier"],
                   size_bytes=int(d["size_bytes"]), sha256=d["sha256"],
                   source=d.get("source", "local"), added=float(d.get("added", 0.0)))


def _measure(path: Path) -> Tuple[int, str]:
    """(size_bytes, sha256) of a real regular file, following no symlink."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"not a regular file: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"empty wordlist: {path}")
    return size, _digest_file(path)


class WordlistStore:
    """A directory of wordlists plus a JSON index. Everything is glass-box: the
    mutating/selecting methods return (result, human-readable reason)."""

    def __init__(self, base_dir, *, now: Optional[Callable[[], float]] = None):
        self.base = Path(base_dir)
        self._now = now or time.time
        self._index = self.base / _INDEX_NAME
        self._items: dict = {}
        self._load()

    # --- persistence ------------------------------------------------------- #

    def _load(self) -> None:
        self._items = {}
        try:
            raw = json.loads(self._index.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return
        for row in raw if isinstance(raw, list) else []:
            try:
                w = Wordlist.from_dict(row)
            except (KeyError, TypeError, ValueError):
                continue
            self._items[w.id] = w

    def _save(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self._index.with_suffix(".json.tmp")
        data = [self._items[k].to_dict() for k in sorted(self._items)]
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._index)

    # --- queries ----------------------------------------------------------- #

    def list(self) -> List[Wordlist]:
        return sorted(self._items.values(), key=lambda w: (TIERS.index(w.tier) if w.tier in TIERS else 99, w.size_bytes))

    def get(self, key: str) -> Optional[Wordlist]:
        if key in self._items:
            return self._items[key]
        for w in self._items.values():
            if w.name == key:
                return w
        return None

    def by_tier(self, tier: str) -> List[Wordlist]:
        return [w for w in self.list() if w.tier == tier]

    # --- mutations --------------------------------------------------------- #

    def register(self, path, *, name: Optional[str] = None, tier: str = "standard",
                 source: str = "local", copy: bool = False) -> Tuple[Optional[Wordlist], str]:
        if tier not in TIERS:
            return None, f"refused: unknown tier '{tier}' (choose {', '.join(TIERS)})"
        src = Path(path)
        if not src.is_absolute():
            return None, f"refused: wordlist path must be absolute: {path}"
        try:
            size, sha = _measure(src)
        except ValueError as e:
            return None, f"refused: {e}"
        dest = src
        if copy:
            self.base.mkdir(parents=True, exist_ok=True)
            dest = self.base / (name or src.name)
            if dest.resolve() != src.resolve():
                shutil.copyfile(src, dest)
        wl = Wordlist(id=sha[:12], name=(name or src.name), path=str(dest),
                      tier=tier, size_bytes=size, sha256=sha, source=source,
                      added=self._now())
        existed = wl.id in self._items
        self._items[wl.id] = wl
        self._save()
        verb = "updated" if existed else "registered"
        return wl, f"{verb} '{wl.name}' [{wl.tier}] id={wl.id} ({size} bytes)"

    def pull_from_nas(self, spec: str, fetcher: Callable[[str, Path], object], *,
                      name: Optional[str] = None, tier: str = "thorough",
                      ) -> Tuple[Optional[Wordlist], str]:
        """Materialize a wordlist named by `spec` from a NAS into the store via an
        injected `fetcher(spec, dest)`. The fetcher owns the transport (SMB/NFS/
        rsync to a QNAP, etc.); this method validates and registers the result, so
        a failed or empty pull registers nothing (fail closed)."""
        self.base.mkdir(parents=True, exist_ok=True)
        dest = self.base / (name or Path(spec).name or "wordlist")
        try:
            fetcher(spec, dest)
        except Exception as e:  # the fetcher is injected/operator code — report, don't crash
            return None, f"refused: NAS pull of '{spec}' failed: {e}"
        if not dest.is_file() or dest.stat().st_size <= 0:
            return None, f"refused: NAS pull of '{spec}' produced no usable file"
        return self.register(dest, name=name, tier=tier, source=f"nas:{spec}")

    def remove(self, key: str) -> Tuple[bool, str]:
        w = self.get(key)
        if w is None:
            return False, f"no wordlist matching '{key}'"
        del self._items[w.id]
        self._save()
        return True, f"removed '{w.name}' id={w.id} (the file on disk was left in place)"

    # --- selection (glass-box) --------------------------------------------- #

    def select(self, *, name: Optional[str] = None, tier: Optional[str] = None,
               ) -> Tuple[Optional[Wordlist], str]:
        """Pick one wordlist for an audit run. Explicit name wins; then tier
        (largest in that tier — the most thorough the operator put there); else the
        fastest available. Fail closed with a reason when nothing qualifies, and
        skip any entry whose file has gone missing since it was registered."""
        # An explicit name gets a specific answer even when it's the only (missing)
        # entry, so check it before the store-empty guard.
        if name is not None:
            w = self.get(name)
            if w is None:
                return None, f"refused: no wordlist named '{name}'"
            if not Path(w.path).is_file():
                return None, f"refused: wordlist '{name}' is registered but its file is missing ({w.path})"
            return w, f"selected '{w.name}' by name (id={w.id}, {w.tier}, {w.size_bytes} bytes)"
        present = [w for w in self.list() if Path(w.path).is_file()]
        if not present:
            return None, "refused: no registered wordlist is present on disk — add or pull one first"
        if tier is not None:
            if tier not in TIERS:
                return None, f"refused: unknown tier '{tier}' (choose {', '.join(TIERS)})"
            tier_items = [w for w in present if w.tier == tier]
            if not tier_items:
                return None, f"refused: no wordlist in tier '{tier}'"
            w = max(tier_items, key=lambda x: x.size_bytes)
            return w, f"selected '{w.name}' as the most thorough in tier '{tier}' (id={w.id}, {w.size_bytes} bytes)"
        # The operator's persisted pick wins over the fastest-available default, so
        # `redux wordlist select --name X` actually sticks for every crack run.
        pick = self.selected()
        if pick is not None:
            return pick, f"using your pick '{pick.name}' (id={pick.id}, {pick.tier}, {pick.size_bytes} bytes)"
        w = min(present, key=lambda x: (TIERS.index(x.tier) if x.tier in TIERS else 99, x.size_bytes))
        return w, f"selected '{w.name}' as the fastest available default (id={w.id}, {w.tier}, {w.size_bytes} bytes)"

    # --- persisted selection ("pick one, it sticks") ---------------------- #

    def _selected_file(self) -> Path:
        return self.base / _SELECTED_NAME

    def selected(self) -> Optional[Wordlist]:
        """The operator's persisted pick (set via set_selected), or None if unset or
        its file has gone missing."""
        try:
            wid = self._selected_file().read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError, ValueError):
            return None
        w = self.get(wid)
        return w if (w is not None and Path(w.path).is_file()) else None

    def set_selected(self, key: str) -> Tuple[Optional[Wordlist], str]:
        """Persist the operator's pick so the crack uses it until changed. Glass-box."""
        w = self.get(key)
        if w is None:
            return None, f"refused: no wordlist named '{key}'"
        if not Path(w.path).is_file():
            return None, f"refused: wordlist '{key}' is registered but its file is missing ({w.path})"
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self._selected_file().with_suffix(".tmp")
        tmp.write_text(w.id, encoding="utf-8")
        os.replace(tmp, self._selected_file())
        return w, f"selected '{w.name}' (id={w.id}, {w.tier}, {w.size_bytes} bytes) — the crack will use this list"

    def clear_selected(self) -> str:
        try:
            self._selected_file().unlink()
            return "cleared the persisted wordlist pick (back to the fastest available)"
        except FileNotFoundError:
            return "no wordlist pick was set"

    def verify(self) -> List[Tuple[Wordlist, str]]:
        """Re-check every registered list: report drift (file missing, or its bytes
        changed since registration). Returns [(wordlist, problem)] — empty == clean."""
        problems = []
        for w in self.list():
            p = Path(w.path)
            if p.is_symlink() or not p.is_file():
                problems.append((w, "file missing"))
                continue
            try:
                _, sha = _measure(p)
            except ValueError as e:
                problems.append((w, str(e)))
                continue
            if sha != w.sha256:
                problems.append((w, "content changed since registration"))
        return problems


def resolve_wordlist(base_dir, *, name: Optional[str] = None, tier: Optional[str] = None,
                     ) -> Tuple[Optional[str], str]:
    """Convenience for callers (e.g. the audit worker): resolve a store selection
    to a concrete absolute path string, or (None, reason) to fail closed."""
    store = WordlistStore(base_dir)
    w, reason = store.select(name=name, tier=tier)
    return (w.path if w else None), reason
