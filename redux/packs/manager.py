"""Pack manager — discover, resolve, enable/disable Packs (task 2.4).

Pure logic over a packs directory: it finds pack manifests, resolves a safe
install/load order from their `requires` (topological, with cycle + missing-dep
detection), and tracks which packs are enabled (persisted to a small JSON state
file). It does not install apt packages or import modules — that's the image side
(Codex's lane) and the supervisor's job; this decides *what* and *in what order*,
glass-box, so a human can see the plan before anything runs.
"""
from __future__ import annotations

import json
import os
import uuid
import time
import fcntl
from .control import read_data, digest, validate_options, compatibility, NAME
from pathlib import Path
from typing import Dict, List, Optional

from .manifest import Pack, load_manifest, MANIFEST_NAMES


class DependencyError(Exception):
    """A pack requires something missing, or the requires form a cycle. Message
    explains exactly which, so it's actionable."""


class PackManager:
    def __init__(self, root, state_path=None, *, write_guard=None):
        self.write_guard = write_guard or (lambda: None)
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
        self.errors = []
        duplicates = set()
        if not self.root.is_dir():
            return []
        started = time.monotonic()
        self.partial = False
        for child in sorted(self.root.iterdir()):
            if time.monotonic() - started > 0.25:
                self.partial = True
                self.errors.append({"state": "unavailable", "reason": "Discovery time budget exceeded; requests refused"})
                break
            if child.is_file() and child.suffix == '.py':
                self.errors.append({"state": "incompatible", "reason": "Legacy Python requires a reviewed adapter manifest", "name": child.stem[:96]})
            if child.is_symlink() or not child.is_dir():
                continue
            for name in MANIFEST_NAMES:
                m = child / name
                if m.is_file():
                    try:
                        pack = load_manifest(m)
                        if pack.name in self._packs or pack.name in duplicates:
                            duplicates.add(pack.name)
                            self._packs.pop(pack.name, None)
                            raise ValueError("duplicate plugin name")
                        self._packs[pack.name] = pack
                    except (OSError, ValueError, TypeError, RecursionError):
                        self.errors.append({"state": "error", "reason": "Invalid or duplicate manifest"})
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

        for target in targets:
            stack = [(target, None, False)]
            while stack:
                name, parent, finished = stack.pop()
                if finished:
                    visiting.discard(name)
                    seen.add(name)
                    order.append(name)
                    continue
                if name in seen:
                    continue
                if name not in self._packs:
                    raise DependencyError(f"'{parent}' requires '{name}', which is not installed")
                if name in visiting:
                    raise DependencyError(f"dependency cycle involving '{name}'")
                visiting.add(name)
                stack.append((name, parent, True))
                stack.extend((dep, name, False) for dep in reversed(self._packs[name].requires))
        return order

    # --- enable / disable -------------------------------------------------- #

    def enable(self, name: str) -> List[str]:
        plan = self.preview("enable", name)
        self.apply(plan["revision"], "enable", name)
        return plan["changed"]

    def disable(self, name: str) -> List[str]:
        plan = self.preview("disable", name)
        self.apply(plan["revision"], "disable", name)
        return plan["changed"]

    def preview(self, action, name, options=None, authorize_side_effects=False):
        if getattr(self, "partial", False):
            raise DependencyError("incomplete discovery; requests refused")
        if name not in self._packs:
            raise DependencyError("pack is not installed")
        desired = set(self._enabled)
        config = dict(self._data.get("configuration", {}))
        if action == "rollback":
            previous = json.loads(read_data(self._state_path.with_name(self._state_path.name + ".previous")))
            if not isinstance(previous, dict) or not isinstance(previous.get("enabled", []), list):
                raise ValueError("invalid rollback snapshot")
            desired = set(previous.get("enabled", []))
            config = previous.get("configuration", {})
            if not isinstance(config, dict):
                raise ValueError("invalid rollback configuration")
        elif action == "enable":
            desired.update(self.resolve_order([name]))
        elif action == "disable":
            desired -= {name} | self._dependents_of(name)
        elif action == "configure":
            validate_options(self._packs[name].options, options)
            config[name] = {**config.get(name, {}), **options}
        else:
            raise ValueError("unknown action")
        for item in desired:
            pack = self._packs.get(item)
            if pack is None:
                raise DependencyError("requested plugin is unavailable")
            if not set(pack.requires).issubset(desired):
                raise DependencyError("missing requested dependency")
            self.resolve_order([item])
            if set(pack.conflicts) & desired:
                raise DependencyError("plugin conflict")
            if compatibility(pack)["state"] == "incompatible":
                raise DependencyError("plugin adapter is incompatible")
            configured = config.get(item, {})
            if not isinstance(configured, dict):
                raise ValueError("invalid saved plugin options")
            known = {key: configured.get(key, spec.get('default')) for key, spec in pack.options.items() if key in configured or 'default' in spec}
            validate_options(pack.options, known)
            if any(spec.get('required') and key not in known for key, spec in pack.options.items()):
                raise ValueError("required configuration missing")
            if pack.requirements:
                raise DependencyError("runtime requirements need external qualification")
            if item not in self._enabled and pack.side_effects and not authorize_side_effects:
                raise DependencyError("explicit side-effect authorization required")
        next_state = {**self._data, "enabled": sorted(desired), "configuration": config}
        revision = digest({"before": self._data, "after": next_state,
                           "catalog": [vars(p) for p in self.list()]})
        return {"revision": revision, "changed": sorted(desired ^ self._enabled),
                "requested": sorted(desired), "activation": "pending controlled runtime activation",
                "next": next_state}

    def apply(self, revision, action, name, options=None, authorize_side_effects=False):
        # Serialize local writers; re-read both catalog and state under the lock.
        self.write_guard()
        parent = self._state_path.parent
        if parent.absolute() != parent.resolve() or self._state_path.is_symlink():
            raise ValueError("unsafe state path")
        lock = self._state_path.with_name(self._state_path.name + ".lock")
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.write_guard()
            fd = os.open(lock.name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            self.discover()
            self._load_state()
            plan = self.preview(action, name, options, authorize_side_effects)
            if revision != plan["revision"]:
                raise ValueError("preview is stale; review a fresh preview")
            self._atomic(self._state_path.with_name(self._state_path.name + ".previous"), self._data)
            self._atomic(self._state_path, plan["next"])
            self._load_state()
        return {"requested": plan["requested"], "activation": plan["activation"]}

    def _atomic(self, path, data):
        raw = json.dumps(data, allow_nan=False).encode()
        if len(raw) > 65536:
            raise ValueError("state exceeds 64 KiB")
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temporary = ".packs-" + uuid.uuid4().hex
        try:
            self.write_guard()
            try:
                info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                import stat
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("unsafe state path")
            except FileNotFoundError:
                pass
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self.write_guard()
            os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)

    def inventory(self, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 0:
            raise ValueError("invalid inventory page")
        records = []
        names = list(self._packs) + sorted(self._enabled - set(self._packs))
        for name in names[offset:offset + limit]:
            pack = self.get(name)
            if pack is None:
                records.append({"name": name, "state": "unavailable", "requested_enabled": True,
                                "active": None, "activation": "unknown", "diagnostics": ["Requested plugin is no longer installed"]})
                continue
            evidence = compatibility(pack)
            missing = [name for name in pack.requires if name not in self._packs]
            diagnostics = []
            if missing:
                diagnostics.append("Missing dependencies: " + ", ".join(missing))
            if pack.requirements:
                diagnostics.append("Platform/runtime requirements are unqualified")
            if set(pack.conflicts) & self._enabled:
                diagnostics.append("Conflicts with requested plugin state")
            from .registry import eligibility
            records.append({"kind": pack.kind, "native": eligibility(pack), "diagnostics": diagnostics, "name": pack.name, "version": pack.version, "description": pack.description,
                "source": "installed manifest", "manifest_sha256": pack.manifest_sha256, "location": pack.root,
                "state": "incompatible" if evidence["state"] == "incompatible" else ("enabled" if self.is_enabled(pack.name) else "disabled"),
                "requested_enabled": self.is_enabled(pack.name), "active": None,
                "activation": ("manual tool; runtime activation not applicable" if pack.kind == "tool"
                               else "unverified; native owner evidence unavailable"), "compatibility": evidence,
                "dependencies": list(pack.requires), "conflicts": list(pack.conflicts),
                "requirements": list(pack.requirements), "side_effects": pack.side_effects,
                "options": {key: {k: v for k, v in spec.items() if k in ("type", "min", "max", "max_length", "sensitive") or (k == "choices" and not spec.get("sensitive"))}
                            for key, spec in pack.options.items()}})
        return {"version": 1, "plugins": records, "errors": self.errors[:32], "error_count": len(self.errors),
                "total": len(names), "offset": offset, "next_offset": offset + limit if offset + limit < len(names) else None,
                "partial": getattr(self, "partial", False),
                "runtime_activation": "unknown"}

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
            data = json.loads(read_data(self._state_path))
        except FileNotFoundError:
            data = {}
        except ValueError:
            if self._state_path.exists():
                raise ValueError("invalid plugin state; preserve it and restore reviewed backup")
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("enabled", []), list) or any(not isinstance(n, str) or not NAME.fullmatch(n) for n in data.get("enabled", [])):
            raise ValueError("invalid plugin state")
        if not isinstance(data.get("configuration", {}), dict):
            raise ValueError("invalid plugin configuration")
        self._data = data
        self._enabled = set(data.get("enabled", []))
